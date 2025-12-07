"""Tidy Preview Image Generator with Multi-Provider Fallback.

Transforms the user's actual photo into a cute, tidy cartoon illustration.
Tries multiple providers: Replicate → Gemini → Hugging Face.
"""
import asyncio
import base64
import io
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Optional

import aiohttp
from PIL import Image

from app.core.config import ConfigManager


logger = logging.getLogger(__name__)


class DreamStateGenerator:
    """Generates cartoon/stylized 'tidy preview' images of the user's actual space.
    
    Multi-provider fallback system:
    1. Replicate (most reliable, actual img2img with SDXL)
    2. Gemini 2.0 Flash (with correct model names)
    3. Hugging Face Inference API (FLUX.1-schnell) - FREE
    
    All providers use the same prompt optimized for tidy room generation.
    """
    
    # Replicate configuration
    REPLICATE_API_URL = "https://api.replicate.com/v1/predictions"
    # SDXL model - extract version hash from model string
    REPLICATE_MODEL = "stability-ai/sdxl:39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"
    REPLICATE_MODEL_VERSION = "39ed52f2a78e934b3ba6e2a89f5b1c712de7dfea535525255b1aa35c5565e08b"
    
    # Gemini models to try in order (with CORRECT names)
    GEMINI_IMAGE_MODELS = [
        "gemini-2.0-flash-exp",  # Current working model with multimodal support
        "gemini-1.5-flash",  # Fallback - supports image input but may not support image output
        "gemini-1.5-pro"  # Another fallback - supports image input but may not support image output
    ]
    
    GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
    HUGGINGFACE_API_URL = "https://router.huggingface.co/hf/black-forest-labs/FLUX.1-schnell"
    
    # Optimized prompt for tidy room generation
    TIDY_PROMPT = (
        "Transform this photo into a perfectly clean and organized room, "
        "remove all clutter and loose items from floor and surfaces, "
        "3D clay render style, cute cartoon aesthetic, smooth matte textures, "
        "warm golden lighting, blender 3d illustration, minimalist furniture, "
        "tidy shelves, soft shadows, isometric view"
    )
    
    def __init__(self, db_path: str = "/data/twinsync.db", data_dir: str = "/data"):
        self.config = ConfigManager(db_path)
        self.data_dir = Path(data_dir)
        self.dream_images_dir = self.data_dir / "dream_states"
    
    def _ensure_dream_images_dir(self):
        """Ensure the dream images directory exists."""
        self.dream_images_dir.mkdir(parents=True, exist_ok=True)
    
    async def generate_dream_state(
        self,
        image_bytes: bytes,
        spot_name: str,
        spot_type: str = "custom",
    ) -> tuple[Optional[str], Optional[str]]:
        """
        Generate a cartoon/stylized version of the user's photo.
        
        Tries multiple providers in order:
        1. Gemini (with correct model names)
        2. Hugging Face Inference API (FREE FLUX.1-schnell)
        3. Local PIL fallback (GUARANTEED to work, no API key needed)
        
        Args:
            image_bytes: The user's actual photo bytes
            spot_name: Name of the spot (for filename)
            spot_type: Type of spot (unused, kept for compatibility)
            
        Returns:
            Tuple of (image_path, error_message). Returns (path, None) on success,
            (None, error_msg) on failure
        """
        logger.info(f"TidyPreview: Starting generation for '{spot_name}'")
        logger.info(f"TidyPreview: Image size: {len(image_bytes)} bytes")
        
        # Validate input
        if not image_bytes or len(image_bytes) < 100:
            error_msg = "Invalid or empty image data"
            logger.error(f"TidyPreview: {error_msg}")
            return None, error_msg
        
        # Validate image first
        try:
            input_image = Image.open(io.BytesIO(image_bytes))
            logger.info(f"TidyPreview: Input image validated, size: {input_image.size}, mode: {input_image.mode}")
        except Exception as e:
            error_msg = f"Invalid image format: {e}"
            logger.error(f"TidyPreview: {error_msg}")
            return None, error_msg
        
        # Try providers in order
        providers = []
        
        # 1. Try Replicate (most reliable, actual img2img)
        replicate_key = await self.config.get_replicate_api_key()
        if not replicate_key:
            replicate_key = os.environ.get("REPLICATE_API_TOKEN", "")
        if replicate_key:
            providers.append(("Replicate", self._try_replicate, replicate_key))
        
        # 2. Try Gemini
        gemini_key = await self.config.get_gemini_api_key()
        if not gemini_key:
            gemini_key = os.environ.get("GEMINI_API_KEY", "")
        if gemini_key:
            providers.append(("Gemini", self._try_gemini, gemini_key))
        
        # 3. Try Hugging Face
        huggingface_key = await self.config.get_huggingface_api_key()
        if not huggingface_key:
            huggingface_key = os.environ.get("HUGGINGFACE_API_KEY", "")
        if huggingface_key:
            providers.append(("Hugging Face", self._try_huggingface, huggingface_key))
        
        logger.info(f"TidyPreview: Found {len(providers)} provider(s) to try: {[p[0] for p in providers]}")
        
        # Try each provider with retry logic
        last_error = None
        for provider_name, provider_func, api_key in providers:
            logger.info(f"TidyPreview: Trying {provider_name}...")
            
            # Retry with exponential backoff for rate limits
            for attempt in range(3):
                try:
                    cartoon_bytes = await provider_func(image_bytes, api_key)
                    
                    if cartoon_bytes:
                        # Validate the generated image
                        try:
                            cartoon_image = Image.open(io.BytesIO(cartoon_bytes))
                            logger.info(f"TidyPreview: {provider_name} succeeded! Generated image size: {cartoon_image.size}")
                            
                            # Save and return
                            image_path = await self._save_dream_image_from_pil(cartoon_image, spot_name)
                            logger.info(f"TidyPreview: Saved successfully at {image_path}")
                            return image_path, None
                        except Exception as e:
                            logger.warning(f"TidyPreview: {provider_name} returned invalid image: {e}")
                            last_error = f"{provider_name} returned invalid image"
                            break  # Don't retry for invalid images
                    else:
                        logger.warning(f"TidyPreview: {provider_name} returned no image (attempt {attempt + 1}/3)")
                        last_error = f"{provider_name} returned no image"
                        
                except aiohttp.ClientResponseError as e:
                    if e.status == 429:  # Rate limited
                        wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                        if provider_name == "Gemini":
                            logger.warning(f"TidyPreview: Gemini free tier rate limited. Trying next provider...")
                        else:
                            logger.warning(f"TidyPreview: {provider_name} rate limited (429), waiting {wait_time}s before retry...")
                        await asyncio.sleep(wait_time)
                        last_error = f"{provider_name} rate limited"
                        continue
                    else:
                        logger.warning(f"TidyPreview: {provider_name} error {e.status}: {e.message}")
                        last_error = f"{provider_name} error {e.status}"
                        break  # Don't retry non-rate-limit errors
                        
                except Exception as e:
                    logger.warning(f"TidyPreview: {provider_name} failed: {e}")
                    last_error = f"{provider_name} failed: {str(e)}"
                    break  # Don't retry unexpected errors
        
        # All providers failed - no more local fallback
        error_msg = f"All image generation providers failed. Please configure at least one API key (Replicate, Gemini, or Hugging Face). Last error: {last_error}"
        logger.error(f"TidyPreview: {error_msg}")
        return None, error_msg
    
    async def _try_gemini(self, image_bytes: bytes, api_key: str) -> Optional[bytes]:
        """Try generating image with Gemini API.
        
        Args:
            image_bytes: Input image bytes
            api_key: Gemini API key
            
        Returns:
            Generated image bytes or None if failed
        """
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        timeout = aiohttp.ClientTimeout(total=120)
        
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for model_name in self.GEMINI_IMAGE_MODELS:
                try:
                    logger.info(f"TidyPreview: Trying Gemini model '{model_name}'...")
                    
                    payload = {
                        "contents": [{
                            "parts": [
                                {"text": self.TIDY_PROMPT},
                                {
                                    "inline_data": {
                                        "mime_type": "image/jpeg",
                                        "data": image_b64
                                    }
                                }
                            ]
                        }],
                        "generationConfig": {
                            "temperature": 0.4,
                            "responseModalities": ["TEXT", "IMAGE"]
                        }
                    }
                    
                    url = f"{self.GEMINI_API_BASE_URL}/{model_name}:generateContent?key={api_key}"
                    
                    async with session.post(url, json=payload) as response:
                        logger.info(f"TidyPreview: Gemini response status: {response.status}")
                        
                        if response.status == 404:
                            logger.warning(f"TidyPreview: Gemini model '{model_name}' not found (404)")
                            continue
                        
                        if response.status == 429:
                            # Rate limited - let caller handle retry
                            response.raise_for_status()
                        
                        if response.status != 200:
                            response_text = await response.text()
                            logger.warning(f"TidyPreview: Gemini error {response.status}: {response_text[:200]}")
                            continue
                        
                        response_text = await response.text()
                        result = json.loads(response_text)
                        
                        # Extract image from response
                        candidates = result.get("candidates", [])
                        if not candidates:
                            logger.warning(f"TidyPreview: Gemini returned no candidates")
                            continue
                        
                        parts = candidates[0].get("content", {}).get("parts", [])
                        
                        # Find the image part
                        for part in parts:
                            if "inlineData" in part:
                                image_data_b64 = part["inlineData"].get("data")
                                if image_data_b64:
                                    logger.info(f"TidyPreview: Gemini model '{model_name}' returned image")
                                    return base64.b64decode(image_data_b64)
                        
                        logger.warning(f"TidyPreview: Gemini model '{model_name}' returned no image data")
                        
                except json.JSONDecodeError as e:
                    logger.warning(f"TidyPreview: Failed to parse Gemini response: {e}")
                    continue
                except aiohttp.ClientError as e:
                    logger.warning(f"TidyPreview: Gemini network error: {e}")
                    raise  # Let caller handle retries
        
        return None
    
    async def _try_huggingface(self, image_bytes: bytes, api_key: str) -> Optional[bytes]:
        """Try generating image with Hugging Face Inference API.
        
        Uses FLUX.1-schnell - this is FREE!
        Note: This is a text-to-image model. The image_bytes parameter is 
        kept for API consistency but not used in generation.
        
        Args:
            image_bytes: Input image bytes (not used - text-to-image only)
            api_key: Hugging Face API key
            
        Returns:
            Generated image bytes or None if failed
        """
        headers = {"Authorization": f"Bearer {api_key}"}
        payload = {"inputs": self.TIDY_PROMPT}
        timeout = aiohttp.ClientTimeout(total=120)
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.HUGGINGFACE_API_URL, headers=headers, json=payload) as response:
                    logger.info(f"TidyPreview: Hugging Face response status: {response.status}")
                    
                    if response.status == 429:
                        # Rate limited - let caller handle retry
                        response.raise_for_status()
                    
                    if response.status == 503:
                        # Model loading - could retry but treat as failure for now
                        logger.warning(f"TidyPreview: Hugging Face model is loading (503)")
                        return None
                    
                    if response.status == 200:
                        # Hugging Face returns raw image bytes
                        image_bytes = await response.read()
                        logger.info(f"TidyPreview: Hugging Face returned {len(image_bytes)} bytes")
                        return image_bytes
                    else:
                        response_text = await response.text()
                        logger.warning(f"TidyPreview: Hugging Face error {response.status}: {response_text[:200]}")
                        return None
                        
        except aiohttp.ClientError as e:
            logger.warning(f"TidyPreview: Hugging Face network error: {e}")
            raise  # Let caller handle retries
        
        return None
    
    async def _try_replicate(self, image_bytes: bytes, api_key: str) -> Optional[bytes]:
        """Generate image using Replicate API with img2img.
        
        Uses the user's actual photo as input and transforms it.
        
        Args:
            image_bytes: Input image bytes
            api_key: Replicate API key
            
        Returns:
            Generated image bytes or None if failed
        """
        # Convert image to base64 data URI
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        image_uri = f"data:image/jpeg;base64,{image_b64}"
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        
        # Use SDXL img2img with the user's image
        payload = {
            "version": self.REPLICATE_MODEL_VERSION,
            "input": {
                "image": image_uri,
                "prompt": self.TIDY_PROMPT,
                "prompt_strength": 0.8,  # How much to transform (0.8 = significant change while keeping structure)
                "num_inference_steps": 30,
                "guidance_scale": 7.5
            }
        }
        
        timeout = aiohttp.ClientTimeout(total=180)  # Replicate can take time
        
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                # Start prediction
                async with session.post(self.REPLICATE_API_URL, headers=headers, json=payload) as response:
                    if response.status != 201:
                        text = await response.text()
                        logger.warning(f"TidyPreview: Replicate create failed {response.status}: {text[:200]}")
                        return None
                    
                    result = await response.json()
                    prediction_url = result.get("urls", {}).get("get")
                    
                    if not prediction_url:
                        logger.warning("TidyPreview: Replicate returned no prediction URL")
                        return None
                
                # Poll for completion
                for _ in range(60):  # Max 60 polls (2 minutes)
                    await asyncio.sleep(2)
                    async with session.get(prediction_url, headers=headers) as poll_response:
                        poll_result = await poll_response.json()
                        status = poll_result.get("status")
                        
                        if status == "succeeded":
                            output = poll_result.get("output")
                            if output and len(output) > 0:
                                image_url = output[0] if isinstance(output, list) else output
                                # Download the image
                                async with session.get(image_url) as img_response:
                                    if img_response.status == 200:
                                        return await img_response.read()
                            return None
                        elif status == "failed":
                            error = poll_result.get("error", "Unknown error")
                            logger.warning(f"TidyPreview: Replicate prediction failed: {error}")
                            return None
                        # else: still processing, continue polling
                
                logger.warning("TidyPreview: Replicate prediction timed out")
                return None
                
        except Exception as e:
            logger.warning(f"TidyPreview: Replicate error: {e}")
            raise
    
    async def _save_dream_image_from_pil(self, pil_image: Image.Image, spot_name: str) -> str:
        """Save a PIL Image and return its relative path."""
        self._ensure_dream_images_dir()
        
        safe_name = "".join(c for c in spot_name if c.isalnum() or c in "._- ").strip()
        safe_name = safe_name.replace(" ", "_")[:30]
        filename = f"tidy_{safe_name}_{uuid.uuid4().hex[:8]}.jpg"
        
        file_path = self.dream_images_dir / filename
        pil_image.save(file_path, "JPEG", quality=90)
        
        logger.info(f"TidyPreview: Saved image to {file_path}")
        return f"/dream-states/{filename}"
    
    def get_dream_image_path(self, relative_path: str) -> Optional[Path]:
        """Get the full path to a dream state image."""
        if not relative_path:
            return None
        
        filename = relative_path.split("/")[-1]
        full_path = self.dream_images_dir / filename
        
        if full_path.exists():
            return full_path
        return None
