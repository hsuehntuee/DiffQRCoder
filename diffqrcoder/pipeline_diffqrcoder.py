from typing import Any, Callable, Dict, List, Optional, Union

import torch
from diffusers import StableDiffusionControlNetPipeline
from diffusers.callbacks import MultiPipelineCallbacks, PipelineCallback
from diffusers.image_processor import PipelineImageInput
from diffusers.models import ControlNetModel
from diffusers.pipelines.stable_diffusion import StableDiffusionPipelineOutput
from diffusers.pipelines.controlnet import MultiControlNetModel
from diffusers.pipelines.stable_diffusion.pipeline_stable_diffusion import retrieve_timesteps
from diffusers.utils import is_torch_xla_available, deprecate
from diffusers.utils.torch_utils import is_compiled_module, is_torch_version
from tqdm import trange

from .image_processor import image_binarize, crop_padding
from .srpg import ScanningRobustPerceptualGuidance


if is_torch_xla_available():
    import torch_xla.core.xla_model as xm

    XLA_AVAILABLE = True
else:
    XLA_AVAILABLE = False


class DiffQRCoderPipeline(StableDiffusionControlNetPipeline):
    def _run_stage1(
        self,
        prompt: Union[str, List[str]] = None,
        qrcode: PipelineImageInput = None,
        height: Optional[int] = None,
        width: Optional[int] = None,
        num_inference_steps: int = 50,
        timesteps: List[int] = None,
        sigmas: List[float] = None,
        guidance_scale: float = 7.5,
        negative_prompt: Optional[Union[str, List[str]]] = None,
        num_images_per_prompt: Optional[int] = 1,
        eta: float = 0.0,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        latents: Optional[torch.Tensor] = None,
        prompt_embeds: Optional[torch.Tensor] = None,
        negative_prompt_embeds: Optional[torch.Tensor] = None,
        ip_adapter_image: Optional[PipelineImageInput] = None,
        ip_adapter_image_embeds: Optional[List[torch.Tensor]] = None,
        output_type: Optional[str] = "pil",
        return_dict: bool = True,
        cross_attention_kwargs: Optional[Dict[str, Any]] = None,
        controlnet_conditioning_scale: Union[float, List[float]] = 1.0,
        guess_mode: bool = False,
        control_guidance_start: Union[float, List[float]] = 0.0,
        control_guidance_end: Union[float, List[float]] = 1.0,
        clip_skip: Optional[int] = None,
        callback_on_step_end: Optional[
            Union[Callable[[int, int, Dict], None], PipelineCallback, MultiPipelineCallbacks]
        ] = None,
        callback_on_step_end_tensor_inputs: List[str] = ["latents"],
        **kwargs,
    ):
        return super().__call__(
            prompt=prompt,
            image=qrcode,
            height=height,
            width=width,
            num_inference_steps=num_inference_steps,
            timesteps=timesteps,
            sigmas=sigmas,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            num_images_per_prompt=num_images_per_prompt,
            eta=eta,
            generator=generator,
            latents=latents,
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            ip_adapter_image=ip_adapter_image,
            ip_adapter_image_embeds=ip_adapter_image_embeds,
            output_type=output_type,
            return_dict=True,
            cross_attention_kwargs=cross_attention_kwargs,
            controlnet_conditioning_scale=controlnet_conditioning_scale,
            guess_mode=guess_mode,
            control_guidance_start=control_guidance_start,
            control_guidance_end=control_guidance_end,
            clip_skip=clip_skip,
            callback_on_step_end=callback_on_step_end,
            callback_on_step_end_tensor_inputs=callback_on_step_end_tensor_inputs,
            **kwargs,
        )

    def _run_stage2(
                self,
                prompt: Union[str, List[str]] = None,
                qrcode: PipelineImageInput = None,
                qrcode_module_size: int = 20,
                qrcode_padding: int = 78,
                ref_image: PipelineImageInput = None,
                height: Optional[int] = None,
                width: Optional[int] = None,
                num_inference_steps: int = 50,
                timesteps: List[int] = None,
                sigmas: List[float] = None,
                guidance_scale: float = 7.5,
                negative_prompt: Optional[Union[str, List[str]]] = None,
                num_images_per_prompt: Optional[int] = 1,
                eta: float = 0.0,
                generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
                latents: Optional[torch.Tensor] = None,
                prompt_embeds: Optional[torch.Tensor] = None,
                negative_prompt_embeds: Optional[torch.Tensor] = None,
                ip_adapter_image: Optional[PipelineImageInput] = None,
                ip_adapter_image_embeds: Optional[List[torch.Tensor]] = None,
                output_type: Optional[str] = "pil",
                return_dict: bool = True,
                cross_attention_kwargs: Optional[Dict[str, Any]] = None,
                controlnet_conditioning_scale: Union[float, List[float]] = 1.0,
                guess_mode: bool = False,
                control_guidance_start: Union[float, List[float]] = 0.0,
                control_guidance_end: Union[float, List[float]] = 1.0,
                scanning_robust_guidance_scale: int = 500,
                perceptual_guidance_scale: int = 10,
                srmpgd_num_iteration: Optional[int] = None,
                srmpgd_lr: Optional[float] = 0.1,
                clip_skip: Optional[int] = None,
                callback_on_step_end: Optional[
                    Union[Callable[[int, int, Dict], None], PipelineCallback, MultiPipelineCallbacks]
                ] = None,
                callback_on_step_end_tensor_inputs: List[str] = ["latents"],
                **kwargs,
            ):

                alpha = kwargs.get('alpha', 0)
                
                callback = kwargs.pop("callback", None)
                callback_steps = kwargs.pop("callback_steps", None)

                if callback is not None:
                    deprecate(
                        "callback",
                        "1.0.0",
                        "Passing `callback` as an input argument to `__call__` is deprecated, consider using `callback_on_step_end`",
                    )
                if callback_steps is not None:
                    deprecate(
                        "callback_steps",
                        "1.0.0",
                        "Passing `callback_steps` as an input argument to `__call__` is deprecated, consider using `callback_on_step_end`",
                    )

                if isinstance(callback_on_step_end, (PipelineCallback, MultiPipelineCallbacks)):
                    callback_on_step_end_tensor_inputs = callback_on_step_end.tensor_inputs

                controlnet = self.controlnet._orig_mod if is_compiled_module(self.controlnet) else self.controlnet

                if not isinstance(control_guidance_start, list) and isinstance(control_guidance_end, list):
                    control_guidance_start = len(control_guidance_end) * [control_guidance_start]
                elif not isinstance(control_guidance_end, list) and isinstance(control_guidance_start, list):
                    control_guidance_end = len(control_guidance_start) * [control_guidance_end]
                elif not isinstance(control_guidance_start, list) and not isinstance(control_guidance_end, list):
                    mult = len(controlnet.nets) if isinstance(controlnet, MultiControlNetModel) else 1
                    control_guidance_start, control_guidance_end = (
                        mult * [control_guidance_start],
                        mult * [control_guidance_end],
                    )

                self.check_inputs(
                    prompt,
                    qrcode,
                    callback_steps,
                    negative_prompt,
                    prompt_embeds,
                    negative_prompt_embeds,
                    ip_adapter_image,
                    ip_adapter_image_embeds,
                    controlnet_conditioning_scale,
                    control_guidance_start,
                    control_guidance_end,
                    callback_on_step_end_tensor_inputs,
                )

                self._guidance_scale = guidance_scale
                self._clip_skip = clip_skip
                self._cross_attention_kwargs = cross_attention_kwargs
                self._interrupt = False

                if prompt is not None and isinstance(prompt, str):
                    batch_size = 1
                elif prompt is not None and isinstance(prompt, list):
                    batch_size = len(prompt)
                else:
                    batch_size = prompt_embeds.shape[0]

                device = self._execution_device

                if isinstance(controlnet, MultiControlNetModel) and isinstance(controlnet_conditioning_scale, float):
                    controlnet_conditioning_scale = [controlnet_conditioning_scale] * len(controlnet.nets)

                global_pool_conditions = (
                    controlnet.config.global_pool_conditions
                    if isinstance(controlnet, ControlNetModel)
                    else controlnet.nets[0].config.global_pool_conditions
                )
                guess_mode = guess_mode or global_pool_conditions

                text_encoder_lora_scale = (
                    self.cross_attention_kwargs.get("scale", None) if self.cross_attention_kwargs is not None else None
                )
                prompt_embeds, negative_prompt_embeds = self.encode_prompt(
                    prompt,
                    device,
                    num_images_per_prompt,
                    self.do_classifier_free_guidance,
                    negative_prompt,
                    prompt_embeds=prompt_embeds,
                    negative_prompt_embeds=negative_prompt_embeds,
                    lora_scale=text_encoder_lora_scale,
                    clip_skip=self.clip_skip,
                )
                if self.do_classifier_free_guidance:
                    prompt_embeds = torch.cat([negative_prompt_embeds, prompt_embeds])

                if ip_adapter_image is not None or ip_adapter_image_embeds is not None:
                    image_embeds = self.prepare_ip_adapter_image_embeds(
                        ip_adapter_image,
                        ip_adapter_image_embeds,
                        device,
                        batch_size * num_images_per_prompt,
                        self.do_classifier_free_guidance,
                    )
                if isinstance(controlnet, ControlNetModel):
                    qrcode = self.prepare_image(
                        image=qrcode,
                        width=width,
                        height=height,
                        batch_size=batch_size * num_images_per_prompt,
                        num_images_per_prompt=num_images_per_prompt,
                        device=device,
                        dtype=controlnet.dtype,
                        do_classifier_free_guidance=self.do_classifier_free_guidance,
                        guess_mode=guess_mode,
                    )
                    height, width = qrcode.shape[-2:]
                elif isinstance(controlnet, MultiControlNetModel):
                    qrcodes = []

                    if isinstance(qrcode[0], list):
                        qrcode = [list(t) for t in zip(*qrcode)]

                    for qrcode_ in qrcode:
                        qrcode_ = self.prepare_image(
                            image=qrcode_,
                            width=width,
                            height=height,
                            batch_size=batch_size * num_images_per_prompt,
                            num_images_per_prompt=num_images_per_prompt,
                            device=device,
                            dtype=controlnet.dtype,
                            do_classifier_free_guidance=self.do_classifier_free_guidance,
                            guess_mode=guess_mode,
                        )

                        qrcodes.append(qrcode_)

                    qrcode = qrcodes
                    height, width = qrcode[0].shape[-2:]
                else:
                    assert False
                qrcode_for_blending = qrcode[qrcode.size(0) // 2, None] if isinstance(qrcode, torch.Tensor) else qrcode[0][qrcode[0].size(0) // 2, None]
                qrcode_latent = self.vae.encode(qrcode_for_blending).latent_dist.sample() * self.vae.config.scaling_factor

                timesteps, num_inference_steps = retrieve_timesteps(
                    self.scheduler, num_inference_steps, device, timesteps, sigmas
                )
                self._num_timesteps = len(timesteps)

                num_channels_latents = self.unet.config.in_channels
                latents = self.prepare_latents(
                    batch_size * num_images_per_prompt,
                    num_channels_latents,
                    height,
                    width,
                    prompt_embeds.dtype,
                    device,
                    generator,
                    latents,
                )

                timestep_cond = None
                if self.unet.config.time_cond_proj_dim is not None:
                    guidance_scale_tensor = torch.tensor(self.guidance_scale - 1).repeat(batch_size * num_images_per_prompt)
                    timestep_cond = self.get_guidance_scale_embedding(
                        guidance_scale_tensor, embedding_dim=self.unet.config.time_cond_proj_dim
                    ).to(device=device, dtype=latents.dtype)

                extra_step_kwargs = self.prepare_extra_step_kwargs(generator, eta)

                added_cond_kwargs = (
                    {"image_embeds": image_embeds}
                    if ip_adapter_image is not None or ip_adapter_image_embeds is not None
                    else None
                )

                controlnet_keep = []
                for i in range(len(timesteps)):
                    keeps = [
                        1.0 - float(i / len(timesteps) < s or (i + 1) / len(timesteps) > e)
                        for s, e in zip(control_guidance_start, control_guidance_end)
                    ]
                    controlnet_keep.append(keeps[0] if isinstance(controlnet, ControlNetModel) else keeps)

                num_warmup_steps = len(timesteps) - num_inference_steps * self.scheduler.order
                is_unet_compiled = is_compiled_module(self.unet)
                is_controlnet_compiled = is_compiled_module(self.controlnet)
                is_torch_higher_equal_2_1 = is_torch_version(">=", "2.1")
                with self.progress_bar(total=num_inference_steps) as progress_bar:
                    with torch.enable_grad():
                        for i, t in enumerate(timesteps):
                            if self.interrupt:
                                continue

                            if (is_unet_compiled and is_controlnet_compiled) and is_torch_higher_equal_2_1:
                                torch._inductor.cudagraph_mark_step_begin()
                            latents = latents.clone().detach().requires_grad_(True)
                            latent_model_input = torch.cat([latents] * 2) if self.do_classifier_free_guidance else latents
                            latent_model_input = self.scheduler.scale_model_input(latent_model_input, t)

                            if guess_mode and self.do_classifier_free_guidance:
                                control_model_input = latents
                                control_model_input = self.scheduler.scale_model_input(control_model_input, t)
                                controlnet_prompt_embeds = prompt_embeds.chunk(2)[1]
                            else:
                                control_model_input = latent_model_input
                                controlnet_prompt_embeds = prompt_embeds

                            if isinstance(controlnet_keep[i], list):
                                cond_scale = [c * s for c, s in zip(controlnet_conditioning_scale, controlnet_keep[i])]
                            else:
                                controlnet_cond_scale = controlnet_conditioning_scale
                                if isinstance(controlnet_cond_scale, list):
                                    controlnet_cond_scale = controlnet_cond_scale[0]
                                cond_scale = controlnet_cond_scale * controlnet_keep[i]

                            down_block_res_samples, mid_block_res_sample = self.controlnet(
                                control_model_input,
                                t,
                                encoder_hidden_states=controlnet_prompt_embeds,
                                controlnet_cond=qrcode,
                                conditioning_scale=cond_scale,
                                guess_mode=guess_mode,
                                return_dict=False,
                            )

                            if guess_mode and self.do_classifier_free_guidance:
                                
                                down_block_res_samples = [torch.cat([torch.zeros_like(d), d]) for d in down_block_res_samples]
                                mid_block_res_sample = torch.cat([torch.zeros_like(mid_block_res_sample), mid_block_res_sample])

                            noise_pred = self.unet(
                                latent_model_input,
                                t,
                                encoder_hidden_states=prompt_embeds,
                                timestep_cond=timestep_cond,
                                cross_attention_kwargs=self.cross_attention_kwargs,
                                down_block_additional_residuals=down_block_res_samples,
                                mid_block_additional_residual=mid_block_res_sample,
                                added_cond_kwargs=added_cond_kwargs,
                                return_dict=False,
                            )[0]

                           
                            if self.do_classifier_free_guidance:
                                noise_pred_uncond, noise_pred_text = noise_pred.chunk(2)
                                noise_pred = noise_pred_uncond + self.guidance_scale * (noise_pred_text - noise_pred_uncond)

                            original_latents = self.scheduler.step(
                                noise_pred,
                                t,
                                latents,
                                **extra_step_kwargs,
                                return_dict=True,
                            ).pred_original_sample

                            blended_latents = original_latents.clone()
                            
                            original_ch0 = original_latents[:, 0:1, :, :]
                            qrcode_ch0 = qrcode_latent[:, 0:1, :, :]
                            
                            qrcode_ch0_normalized = (qrcode_ch0 - qrcode_ch0.mean()) / (qrcode_ch0.std() + 1e-8)
                            qrcode_ch0_normalized = qrcode_ch0_normalized * original_ch0.std() + original_ch0.mean()
                            
                            blended_latents[:, 0:1, :, :] = (1 - alpha) * original_ch0 + alpha * qrcode_ch0_normalized

                            timesteps_prev = t - self.scheduler.config.num_train_timesteps // self.scheduler.num_inference_steps
                            alpha_prod_t = self.scheduler.alphas_cumprod[t]
                            beta_prod_t = 1 - alpha_prod_t
                            alpha_prod_t_prev = self.scheduler.alphas_cumprod[timesteps_prev] if timesteps_prev >= 0 else self.scheduler.final_alpha_cumprod
                            beta_prod_t_prev = 1 - alpha_prod_t_prev

                            latents = (alpha_prod_t_prev ** 0.5) * blended_latents + (beta_prod_t_prev ** 0.5) * noise_pred

                            if callback_on_step_end is not None:
                                callback_kwargs = {}
                                for k in callback_on_step_end_tensor_inputs:
                                    callback_kwargs[k] = locals()[k]
                                callback_outputs = callback_on_step_end(self, i, t, callback_kwargs)

                                latents = callback_outputs.pop("latents", latents)
                                prompt_embeds = callback_outputs.pop("prompt_embeds", prompt_embeds)
                                negative_prompt_embeds = callback_outputs.pop("negative_prompt_embeds", negative_prompt_embeds)

                            if i == len(timesteps) - 1 or ((i + 1) > num_warmup_steps and (i + 1) % self.scheduler.order == 0):
                                progress_bar.update()
                                if callback is not None and i % callback_steps == 0:
                                    step_idx = i // getattr(self.scheduler, "order", 1)
                                    callback(step_idx, t, latents)

                            if XLA_AVAILABLE:
                                xm.mark_step()

                if hasattr(self, "final_offload_hook") and self.final_offload_hook is not None:
                    self.unet.to("cpu")
                    self.controlnet.to("cpu")
                    torch.cuda.empty_cache()

                if not output_type == "latent":
                    image = self.vae.decode(latents / self.vae.config.scaling_factor, return_dict=False, generator=generator)[
                        0
                    ]
                    image, has_nsfw_concept = self.run_safety_checker(image, device, prompt_embeds.dtype)
                else:
                    image = latents
                    has_nsfw_concept = None

                if has_nsfw_concept is None:
                    do_denormalize = [True] * image.shape[0]
                else:
                    do_denormalize = [not has_nsfw for has_nsfw in has_nsfw_concept]

                image = self.image_processor.postprocess(image, output_type=output_type, do_denormalize=do_denormalize)

                self.maybe_free_model_hooks()

                if not return_dict:
                    return (image, has_nsfw_concept)

                return StableDiffusionPipelineOutput(images=image, nsfw_content_detected=has_nsfw_concept)

    

    @torch.no_grad()
    def __call__(
        self,
        prompt: Union[str, List[str]] = None,
        qrcode: PipelineImageInput = None,
        qrcode_module_size: int = 20,
        qrcode_padding: int = 78,
        height: Optional[int] = None,
        width: Optional[int] = None,
        num_inference_steps: int = 50,
        timesteps: List[int] = None,
        sigmas: List[float] = None,
        guidance_scale: float = 7.5,
        negative_prompt: Optional[Union[str, List[str]]] = None,
        num_images_per_prompt: Optional[int] = 1,
        eta: float = 0.0,
        generator: Optional[Union[torch.Generator, List[torch.Generator]]] = None,
        latents: Optional[torch.Tensor] = None,
        prompt_embeds: Optional[torch.Tensor] = None,
        negative_prompt_embeds: Optional[torch.Tensor] = None,
        ip_adapter_image: Optional[PipelineImageInput] = None,
        ip_adapter_image_embeds: Optional[List[torch.Tensor]] = None,
        output_type: Optional[str] = "pil",
        return_dict: bool = True,
        cross_attention_kwargs: Optional[Dict[str, Any]] = None,
        controlnet_conditioning_scale: Union[float, List[float]] = 1.0,
        guess_mode: bool = False,
        control_guidance_start: Union[float, List[float]] = 0.0,
        control_guidance_end: Union[float, List[float]] = 1.0,
        scanning_robust_guidance_scale: int = 500,
        perceptual_guidance_scale: int = 10,
        clip_skip: Optional[int] = None,
        srmpgd_num_iteration: Optional[int] = None,
        srmpgd_lr: Optional[float] = 0.1,
        callback_on_step_end: Optional[
            Union[Callable[[int, int, Dict], None], PipelineCallback, MultiPipelineCallbacks]
        ] = None,
        callback_on_step_end_tensor_inputs: List[str] = ["latents"],
        **kwargs,
    ):
        stage1_output = self._run_stage1(
            prompt=prompt,
            qrcode=qrcode,
            height=height,
            width=width,
            num_inference_steps=num_inference_steps,
            timesteps=timesteps,
            sigmas=sigmas,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            num_images_per_prompt=num_images_per_prompt,
            eta=eta,
            generator=generator,
            latents=latents,
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            ip_adapter_image=ip_adapter_image,
            ip_adapter_image_embeds=ip_adapter_image_embeds,
            output_type="pt",
            return_dict=False,
            cross_attention_kwargs=cross_attention_kwargs,
            controlnet_conditioning_scale=controlnet_conditioning_scale,
            guess_mode=guess_mode,
            control_guidance_start=control_guidance_start,
            control_guidance_end=control_guidance_end,
            clip_skip=clip_skip,
            callback_on_step_end=callback_on_step_end,
            callback_on_step_end_tensor_inputs=callback_on_step_end_tensor_inputs,
        )
        import os
        from PIL import Image
        
        # 轉換stage1輸出為PIL image並保存
        stage1_image_tensor = stage1_output.images[0]  # 取第一張圖片
        stage1_pil = self.image_processor.postprocess(
            stage1_image_tensor.unsqueeze(0), 
            output_type="pil", 
            do_denormalize=[True]
        )[0]
        
        save_path = os.path.join(os.getcwd(), "stage1_original_output.png")
        stage1_pil.save(save_path)
        print(f"Stage1 original image saved to: {save_path}")
        stage2_output = self._run_stage2(
            prompt=prompt,
            qrcode=qrcode,
            qrcode_module_size=qrcode_module_size,
            qrcode_padding=qrcode_padding,
            ref_image=stage1_output.images,
            height=height,
            width=width,
            num_inference_steps=num_inference_steps,
            timesteps=timesteps,
            sigmas=sigmas,
            guidance_scale=guidance_scale,
            negative_prompt=negative_prompt,
            num_images_per_prompt=num_images_per_prompt,
            eta=eta,
            generator=generator,
            latents=latents,
            prompt_embeds=prompt_embeds,
            negative_prompt_embeds=negative_prompt_embeds,
            ip_adapter_image=ip_adapter_image,
            ip_adapter_image_embeds=ip_adapter_image_embeds,
            output_type=output_type,
            return_dict=return_dict,
            cross_attention_kwargs=cross_attention_kwargs,
            controlnet_conditioning_scale=controlnet_conditioning_scale,
            guess_mode=guess_mode,
            control_guidance_start=control_guidance_start,
            control_guidance_end=control_guidance_end,
            scanning_robust_guidance_scale=scanning_robust_guidance_scale,
            perceptual_guidance_scale=perceptual_guidance_scale,
            clip_skip=clip_skip,
            srmpgd_num_iteration=srmpgd_num_iteration,
            callback_on_step_end=callback_on_step_end,
            callback_on_step_end_tensor_inputs=callback_on_step_end_tensor_inputs,
        )
        return stage2_output
