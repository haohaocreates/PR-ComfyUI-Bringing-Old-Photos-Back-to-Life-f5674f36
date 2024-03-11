import os
import gc
import glob
import warnings

import torch
from torch.utils.data import DataLoader
import torchvision
from PIL import Image
import numpy as np

from .Global import detection as ScratchDetector

from .Global import test as Restorer
from .Global.options.test_options import TestOptions as RestoreOptions

try:
    from .Face_Detection import detect_all_dlib as FaceDetector
    from .Face_Detection import align_warp_back_multiple_dlib as FaceBlender
except Exception as error:
    warnings.warn("BOPBTL: Unable to import Face_Detection. You may need to install Dlib with 'pip install dlib'.")

from .Face_Enhancement import test_face as FaceEnhancer
from .Face_Enhancement.options.test_options import TestOptions as FaceEnhancerOptions
from .Face_Enhancement.data.face_dataset import FaceTensorDataset

import comfy.model_management
import folder_paths

def search_custom_model_dir(dir:str , ext: str): # The issue with this is that it is hardcoded
    models = []
    if os.path.isdir(dir):
        models = glob.glob(dir + os.sep + "**" + os.sep + "*" + ext, recursive=True)
        models = [path[len(dir):] for path in models]
    return models

def tensor_images_to_numpy(images: torch.Tensor):
    images = images.permute(0, 3, 1, 2)
    np_images = []
    for image in images:
        pil_image = torchvision.transforms.ToPILImage()(image)
        np_image = np.array(pil_image)
        np_images.append(np_image)
    return np_images

UPSCALE_METHODS = {
    "nearest-exact": Image.Resampling.NEAREST, 
    "bilinear": Image.Resampling.BILINEAR, 
    "area" : Image.Resampling.BOX, 
    "bicubic" : Image.Resampling.BICUBIC, 
    "lanczos" : Image.Resampling.LANCZOS, 
}

class LoadScratchMaskModel:
    RETURN_TYPES = ("SCRATCH_MODEL",)
    RETURN_NAMES = ("scratch_model",)
    FUNCTION = "run"
    OUTPUT_NODE = True

    SCRATCH_MODELS_PATH = "." + os.sep + "models" + os.sep + "scratch_models" + os.sep

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "scratch_model": (search_custom_model_dir(self.SCRATCH_MODELS_PATH, ".pt"),),
            },
        }

    @staticmethod
    def load_model(model_path: str):
        model = ScratchDetector.load_model(
            device_ids=comfy.model_management.get_torch_device(), 
            checkpoint_path=model_path, 
        )
        return (model,)

    def run(self, scratch_model: str):
        model_path = self.SCRATCH_MODELS_PATH + scratch_model
        return LoadScratchMaskModel.load_model(model_path)

class ScratchMask:
    RETURN_TYPES = ("MASK",)
    RETURN_NAMES = ("mask",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    INPUT_SIZE_METHODS = ["full_size", "resize_256", "scale_256"]

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "scratch_model": ("SCRATCH_MODEL",),
                "image": ("IMAGE",),
                "input_size": (
                    self.INPUT_SIZE_METHODS, {
                    "default": self.INPUT_SIZE_METHODS[0],
                }),
                "resize_method": (
                    list(UPSCALE_METHODS.keys()), {
                    "default": "bilinear",
                }),
            },
        }

    @staticmethod
    def detect_scratches(
        scratch_model,
        image: torch.Tensor, 
        input_size: str, 
        resize_method: str, 
    ):
        image = image.permute(0, 3, 1, 2)
        masks = []
        for i in range(image.size()[0]):
            masks.append(ScratchDetector.detect_scratches(
                image=torchvision.transforms.ToPILImage()(image[i]), 
                model=scratch_model,
                device_ids=comfy.model_management.get_torch_device(), 
                input_size=input_size, 
                resize_method=UPSCALE_METHODS[resize_method], 
            ))
        masks = torch.stack(masks)
        masks = masks.permute(1, 0, 2, 3)[0]

        return (masks,)

    def run(self, scratch_model, image, input_size, resize_method):
        return ScratchMask.detect_scratches(
            scratch_model, 
            image, 
            input_size, 
            resize_method, 
        )

class LoadRestoreOldPhotosModel:
    RETURN_TYPES = ("BOPBTL_MODELS",)
    RETURN_NAMES = ("bopbtl_models",)
    FUNCTION = "run"
    OUTPUT_NODE = True

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "device_ids": ("STRING", {"default": "0"}), # TODO: can this be automated away?
                "use_scratch_detection": ( # TODO: is this needed this early?
                    ["True", "False"], {
                    "default": "False",
                }),
                "mapping_patch_attention": (
                    ["True", "False"], {
                    "default": "False",
                }),
                "mapping_net": (folder_paths.get_filename_list("checkpoints"),),
                "vae_b": (folder_paths.get_filename_list("vae"),),
                "vae_a": (folder_paths.get_filename_list("vae"),),
            },
        }

    @staticmethod
    def load_models(
        device_id_list, 
        use_scratch_detection: bool, 
        mapping_patch_attention: bool, 
        mapping_net_path: str, 
        vae_b_path: str, 
        vae_a_path: str, 
    ):
        opt = RestoreOptions()
        opt.initialize()
        opt = opt.parser.parse_args("")
        opt.isTrain = False
        opt.test_mode = "Full"

        opt.Quality_restore = not use_scratch_detection
        opt.Scratch_and_Quality_restore = use_scratch_detection

        opt.test_vae_a = vae_a_path
        opt.test_vae_b = vae_b_path
        opt.test_mapping_net = mapping_net_path
        opt.HR = mapping_patch_attention
        opt.gpu_ids = device_id_list

        #opt.test_vae_a = "./checkpoints/restoration/VAE_A_quality/latest_net_G.pth"
        #if opt.Quality_restore:
        #    opt.test_vae_b = "./checkpoints/restoration/VAE_B_quality/latest_net_G.pth"
        #    opt.test_mapping_net = "./checkpoints/restoration/mapping_quality/latest_net_mapping_net.pth"
        #if opt.Scratch_and_Quality_restore:
        #    opt.test_vae_b = "./checkpoints/restoration/VAE_B_scratch/latest_net_G.pth"
        #    opt.test_mapping_net = "./checkpoints/restoration/mapping_scratch/latest_net_mapping_net.pth"
        #if opt.HR:
        #    opt.test_mapping_net = "./checkpoints/restoration/mapping_Patch_Attention/latest_net_mapping_net.pth"

        Restorer.parameter_set(opt)
        model = Restorer.load_model(opt)
        image_transform, mask_transform = Restorer.get_transforms()
        return((opt, model, image_transform, mask_transform),)

    def run(
        self, 
        device_ids: str, 
        use_scratch_detection: str, 
        mapping_patch_attention: str, 
        mapping_net, 
        vae_b, 
        vae_a
    ):
        return LoadRestoreOldPhotosModel.load_models(
            [int(n) for n in device_ids.split(",")], 
            True if use_scratch_detection == "True" else False, 
            True if mapping_patch_attention == "True" else False, 
            folder_paths.get_full_path("checkpoints", mapping_net), 
            folder_paths.get_full_path("vae", vae_b), 
            folder_paths.get_full_path("vae", vae_a), 
        )

class RestoreOldPhotos:
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("image",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "bopbtl_models": ("BOPBTL_MODELS",),
                "image": ("IMAGE",),
            },
            "optional": {
                "scratch_mask": ("MASK",),
            },
        }

    @staticmethod
    def restore(image: torch.Tensor, bopbtl_models, scratch_mask: torch.Tensor = None):
        (opt, model, image_transform, mask_transform) = bopbtl_models

        image = image.permute(0, 3, 1, 2)
        restored_images = []
        for i in range(image.size()[0]):
            pil_image = torchvision.transforms.ToPILImage()(image[i]).convert("RGB")
            if scratch_mask is None:
                transformed_image, transformed_mask, _ = Restorer.transform_image(
                    pil_image, 
                    image_transform, 
                    opt.test_mode, 
                )
            else:
                mask = torch.stack([scratch_mask[i]])
                pil_mask = torchvision.transforms.ToPILImage()(mask).convert("RGB")
                transformed_image, transformed_mask, _ = Restorer.transform_image_and_mask(
                    pil_image, 
                    image_transform, 
                    pil_mask, 
                    mask_transform, 
                    opt.mask_dilation, 
                )
            with torch.no_grad():
                restored_image = model.inference(transformed_image, transformed_mask)[0]
                restored_image = (restored_image + 1.0) / 2.0
                restored_images.append(restored_image)
        restored_images = torch.stack(restored_images)
        restored_images = restored_images.permute(0, 2, 3, 1)
        return (restored_images,)

    def run(self, image, bopbtl_models, scratch_mask = None):
        return RestoreOldPhotos.restore(image, bopbtl_models, scratch_mask)

class LoadFaceDetector:
    RETURN_TYPES = ("DLIB_MODEL",)
    RETURN_NAMES = ("dlib_model",)
    FUNCTION = "run"
    OUTPUT_NODE = True

    def __init__(self):
        pass

    FACE_MODEL_PATH = "." + os.sep + "models" + os.sep + "facedetection" + os.sep

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "shape_predictor_68_face_landmarks": (search_custom_model_dir(self.FACE_MODEL_PATH, ".dat"),),
            },
        }

    @staticmethod
    def load_model(model_path: str):
        face_detector = FaceDetector.dlib.get_frontal_face_detector()
        landmark_locator = FaceDetector.dlib.shape_predictor(model_path)
        return ((face_detector, landmark_locator),)

    def run(self, shape_predictor_68_face_landmarks: str):
        model_path = self.FACE_MODEL_PATH + shape_predictor_68_face_landmarks
        return LoadFaceDetector.load_model(model_path)

class DetectFaces:
    RETURN_TYPES = ("FACE_COUNT", "IMAGE", "FACE_LANDMARKS")
    RETURN_NAMES = ("face_count", "cropped_faces", "face_landmarks")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "dlib_model": ("DLIB_MODEL",),
                "images": ("IMAGE",),
                "face_size": (
                    ["256", "512"], {
                    "default": "512",
                }),
            },
        }

    @staticmethod
    def detect_faces_batch(dlib_model, images: torch.Tensor, face_size: str):
        (face_detector, landmark_locator) = dlib_model
        face_size = int(face_size)

        images = images.permute(0, 3, 1, 2)

        face_counts = []
        aligned_faces = []
        faces_landmarks = []
        for image in images:
            pil_image = torchvision.transforms.ToPILImage()(image).convert("RGB")
            np_image = np.array(pil_image)

            landmarks = FaceDetector.get_face_landmarks(face_detector, landmark_locator, np_image)
            np_faces = FaceDetector.get_aligned_faces(landmarks,  np_image, face_size)

            faces = [torch.from_numpy(np_face) for np_face in np_faces]
            face_counts.append(len(faces))
            aligned_faces += faces
            faces_landmarks += landmarks
        aligned_faces = torch.stack(aligned_faces)

        return (face_counts, aligned_faces, faces_landmarks)

    def run(self, dlib_model, images, face_size):
        return DetectFaces.detect_faces_batch(dlib_model, images, face_size)

class LoadFaceEnhancer:
    RETURN_TYPES = ("FACE_ENHANCE_MODEL",)
    RETURN_NAMES = ("face_enhance_model",)
    FUNCTION = "run"
    OUTPUT_NODE = True

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "device_ids": ("STRING", {"default": "0"}), # TODO: can this be automated away?
                "face_enhance_model": (folder_paths.get_filename_list("checkpoints"),),
                "model_face_size": (["256", "512"], {"default": "512"}),
            },
        }

    @staticmethod
    def load_model(device_ids: str, face_enhance_model: str, model_face_size: str):
        load_size = int(model_face_size)

        opt = FaceEnhancerOptions().parse(args="")
        opt.isTrain = False

        opt.gpu_ids = [int(n) for n in device_ids.split(",")]
        opt.label_nc = 18
        opt.no_instance = True
        opt.preprocess_model = "resize"
        opt.test_path_G = folder_paths.get_full_path("checkpoints", face_enhance_model)
        opt.no_parsing_map = True

        opt.load_size = load_size # this is required to create the model correctly
        #opt.batchSize = batch_size

        model = FaceEnhancer.load_model(opt)

        return ((model, load_size),)

    def run(self, device_ids, face_enhance_model, model_face_size):
        return LoadFaceEnhancer.load_model(device_ids, face_enhance_model, model_face_size)

class EnhanceFaces:
    RETURN_TYPES = ("FACE_COUNT", "IMAGE")
    RETURN_NAMES = ("face_count", "enhanced_cropped_faces")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "face_enhance_model": ("FACE_ENHANCE_MODEL",),
                "face_count": ("FACE_COUNT",),
                "cropped_faces": ("IMAGE",),
            },
            "optional": {
                "face_parts": ("IMAGE,"),
            },
        }

    def enhance_faces(
        face_enhance_model, 
        face_count, 
        cropped_faces: torch.Tensor, 
        face_parts = [], 
    ):
        model, load_size = face_enhance_model
        if load_size == 512:
            batch_size = 1
        elif load_size == 256:
            batch_size = 4
        else:
            raise NotImplementedError("Unknown model face size!")

        cropped_faces = cropped_faces.permute(0, 3, 1, 2)
        image_list = []
        for image in cropped_faces:
            pil_image = torchvision.transforms.ToPILImage()(image)
            image_list.append(pil_image)

        parts_list = face_parts
        if len(parts_list) == 0:
            parts_list = [None for i in range(len(FaceTensorDataset.get_parts()))]
        else:
            for i in range(len(parts_list)):
                part = parts_list[i]
                if part is None:
                    continue
                parts_list[i] = torchvision.transforms.ToPILImage()(part)

        dataset = FaceTensorDataset()
        dataset.initialize(
            preprocess_mode="scale_width_and_crop", 
            load_size=load_size, 
            crop_size=load_size, 
            aspect_ratio=1.0, 
            is_train=False, 
            no_flip=True, 
            image_list=image_list, 
            parts_list=parts_list, 
        )
        dataloader = DataLoader(
            dataset, 
            batch_size=batch_size, 
            shuffle=False, 
        )

        enhanced_faces = []
        for batch in dataloader:
            enhanced_face_batch = model(batch, mode="inference")
            enhanced_faces += enhanced_face_batch
        enhanced_faces = torch.stack(enhanced_faces)
        enhanced_faces = enhanced_faces.permute(0, 2, 3, 1)
        for i in range(len(enhanced_faces)):
            enhanced_faces[i] = (enhanced_faces[i] + 1) / 2

        return (face_count, enhanced_faces)

    def run(
        self, 
        face_enhance_model, 
        face_count, 
        cropped_faces, 
        face_parts = [], # fallback
        part_skin: torch.Tensor = None, 
        part_hair: torch.Tensor = None, 
        part_l_brow: torch.Tensor = None, 
        part_r_brow: torch.Tensor = None, 
        part_l_eye: torch.Tensor = None, 
        part_r_eye: torch.Tensor = None, 
        part_eye_g: torch.Tensor = None, 
        part_l_ear: torch.Tensor = None, 
        part_r_ear: torch.Tensor = None, 
        part_ear_r: torch.Tensor = None, 
        part_nose: torch.Tensor = None, 
        part_mouth: torch.Tensor = None, 
        part_u_lip: torch.Tensor = None, 
        part_l_lip: torch.Tensor = None, 
        part_neck: torch.Tensor = None, 
        part_neck_l: torch.Tensor = None, 
        part_cloth: torch.Tensor = None, 
        part_hat: torch.Tensor = None, 
    ):
        parts = [
            part_skin,
            part_hair, 
            part_l_brow, 
            part_r_brow, 
            part_l_eye, 
            part_r_eye, 
            part_eye_g, 
            part_l_ear, 
            part_r_ear, 
            part_ear_r, 
            part_nose, 
            part_mouth, 
            part_u_lip, 
            part_l_lip, 
            part_neck, 
            part_neck_l, 
            part_cloth, 
            part_hat, 
        ]
        if len(face_parts) > 0:
            if len(face_parts) != len(parts):
                # this can't check if parts are correct "type"
                raise RuntimeError("Parts do not match!")
            for i in range(len(parts)):
                if parts[i] is None:
                    parts[i] = face_parts[i]
        return EnhanceFaces.enhance_faces(
            face_enhance_model, 
            face_count, 
            cropped_faces, 
            parts,
        )

class EnhanceFacesAdvanced(EnhanceFaces):
    @classmethod
    def INPUT_TYPES(self):
        input_types = super().INPUT_TYPES()
        optional = input_types["optional"]
        parts = FaceTensorDataset.get_parts()
        for part in parts:
            optional["part_" + part] = ("IMAGE,")
        return input_types

class BlendFaces:
    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("images",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "original_image": ("IMAGE",),
                "face_count": ("FACE_COUNT",),
                "enhanced_cropped_faces": ("IMAGE",),
                "face_landmarks": ("FACE_LANDMARKS",),
            },
        }

    @staticmethod
    def blend_faces(
        original_image: torch.Tensor, 
        face_count, 
        enhanced_cropped_faces: torch.Tensor, 
        face_landmarks, 
    ):
        face_size = enhanced_cropped_faces.size()[2]
        np_image = tensor_images_to_numpy(original_image)
        np_enhanced_face = tensor_images_to_numpy(enhanced_cropped_faces)
        blended_images = FaceBlender.blend_faces(
            np_image, 
            face_count, 
            np_enhanced_face, 
            face_landmarks, 
            face_size, 
        )
        blended_images = [torch.from_numpy(img) for img in blended_images]
        blended_images = torch.stack(blended_images)
        return (blended_images,)

    def run(self, original_image, face_count, enhanced_cropped_faces, face_landmarks):
        return BlendFaces.blend_faces(
            original_image, 
            face_count, 
            enhanced_cropped_faces, 
            face_landmarks, 
        )

class DetectRestoreBlendFaces:
    RETURN_TYPES = ("IMAGE", )
    RETURN_NAMES = ("image", )
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "image"

    def __init__(self):
        pass

    @classmethod
    def INPUT_TYPES(self):
        return {
            "required": {
                "dlib_model": ("DLIB_MODEL",),
                "face_enhance_model": ("FACE_ENHANCE_MODEL",),
                "image": ("IMAGE",),
            },
        }

    def run(self, image: torch.Tensor, dlib_model, face_enhance_model):
        _, load_size = face_enhance_model
        face_count, cropped_faces, face_landmarks = DetectFaces.detect_faces_batch(dlib_model, image, load_size)
        _, enhanced_faces = EnhanceFaces.enhance_faces(face_enhance_model, face_count, cropped_faces)
        return BlendFaces.blend_faces(image, face_count, enhanced_faces, face_landmarks)

NODE_CLASS_MAPPINGS = {
    "BOPBTL_ScratchMask": ScratchMask,
    "BOPBTL_LoadScratchMaskModel": LoadScratchMaskModel,
    "BOPBTL_LoadRestoreOldPhotosModel": LoadRestoreOldPhotosModel,
    "BOPBTL_RestoreOldPhotos": RestoreOldPhotos,
    "BOPBTL_LoadFaceDetector": LoadFaceDetector,
    "BOPBTL_DetectFaces": DetectFaces,
    "BOPBTL_LoadFaceEnhancer": LoadFaceEnhancer,
    "BOPBTL_EnhanceFaces": EnhanceFaces,
    "BOPBTL_EnhanceFacesAdvanced": EnhanceFacesAdvanced,
    "BOPBTL_BlendFaces": BlendFaces,
    "BOPBTL_DetectRestoreBlendFaces": DetectRestoreBlendFaces,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "BOPBTL_ScratchMask": "Scratch Mask",
    "BOPBTL_LoadScratchMaskModel": "Load Scratch Mask Model",
    "BOPBTL_LoadRestoreOldPhotosModel": "Load Restore Old Photos Model",
    "BOPBTL_RestoreOldPhotos": "Restore Old Photos",
    "BOPBTL_LoadFaceDetector": "Load Face Detector (Dlib)",
    "BOPBTL_DetectFaces": "Detect Faces (Dlib)",
    "BOPBTL_LoadFaceEnhancer": "Load Face Enhancer",
    "BOPBTL_EnhanceFaces": "Enhance Faces",
    "BOPBTL_EnhanceFacesAdvanced": "Enhance Faces (Advanced)",
    "BOPBTL_BlendFaces": "Blend Faces (Dlib)",
    "BOPBTL_DetectRestoreBlendFaces": "Detect-Restore-Blend Faces (dlib)",
}