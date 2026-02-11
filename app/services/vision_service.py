"""Vision model service for image analysis using Groq Llama 3.2 Vision."""

import base64
import logging
from io import BytesIO
from typing import Optional

from PIL import Image

from app.config import get_settings
from app.llm_client import get_llm_client

logger = logging.getLogger(__name__)
settings = get_settings()


class VisionService:
    """Service for analyzing images with Groq Vision model."""
    
    def __init__(self):
        """Initialize vision service."""
        self.llm_client = get_llm_client()
        self.max_image_size = (1024, 1024)  # Max dimensions
        self.max_file_size = 5 * 1024 * 1024  # 5MB
        
    def validate_image(self, file_bytes: bytes, filename: str) -> tuple[bool, Optional[str]]:
        """
        Validate image file.
        
        Args:
            file_bytes: Image file bytes
            filename: Original filename
            
        Returns:
            (is_valid, error_message)
        """
        # Check file size
        if len(file_bytes) > self.max_file_size:
            return False, f"Image too large. Maximum size is {self.max_file_size // (1024*1024)}MB"
        
        # Check if it's a valid image
        try:
            img = Image.open(BytesIO(file_bytes))
            img.verify()
            
            # Check format
            valid_formats = {'PNG', 'JPEG', 'JPG', 'WEBP', 'GIF'}
            if img.format not in valid_formats:
                return False, f"Unsupported format: {img.format}. Use PNG, JPEG, WEBP, or GIF"
            
            return True, None
            
        except Exception as e:
            logger.error(f"Image validation failed: {e}")
            return False, f"Invalid image file: {str(e)}"
    
    def process_image(self, file_bytes: bytes) -> str:
        """
        Process and optimize image for vision model.
        
        Args:
            file_bytes: Raw image bytes
            
        Returns:
            Base64 encoded image string
        """
        try:
            # Open image
            img = Image.open(BytesIO(file_bytes))
            
            # Convert to RGB if needed (e.g., PNG with transparency)
            if img.mode in ('RGBA', 'LA', 'P'):
                background = Image.new('RGB', img.size, (255, 255, 255))
                if img.mode == 'P':
                    img = img.convert('RGBA')
                background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                img = background
            elif img.mode != 'RGB':
                img = img.convert('RGB')
            
            # Resize if too large
            if img.size[0] > self.max_image_size[0] or img.size[1] > self.max_image_size[1]:
                img.thumbnail(self.max_image_size, Image.Resampling.LANCZOS)
                logger.info(f"Resized image to {img.size}")
            
            # Convert to JPEG for optimal size
            buffer = BytesIO()
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            img_bytes = buffer.getvalue()
            
            # Encode to base64
            base64_image = base64.b64encode(img_bytes).decode('utf-8')
            
            logger.info(f"Processed image: {len(base64_image)} chars base64")
            return base64_image
            
        except Exception as e:
            logger.error(f"Image processing failed: {e}", exc_info=True)
            raise ValueError(f"Failed to process image: {str(e)}")
    
    async def analyze_image(
        self,
        image_base64: str,
        prompt: str = "Describe this image in detail.",
        user_id: str = None
    ) -> dict:
        """
        Analyze image using Groq Vision model.
        
        Args:
            image_base64: Base64 encoded image
            prompt: Analysis prompt
            user_id: Optional user ID for logging
            
        Returns:
            Analysis result with text and metadata
        """
        try:
            logger.info(f"Analyzing image for user {user_id} with prompt: {prompt[:50]}...")
            
            # Prepare messages for vision model
            messages = [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": prompt
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{image_base64}"
                            }
                        }
                    ]
                }
            ]
            
            # Call Groq Vision model (Llama 4 Scout)
            response = await self.llm_client.generate_completion_async(
                messages=messages,
                model="meta-llama/llama-4-scout-17b-16e-instruct",
                temperature=0.7,
                max_tokens=1000
            )
            
            analysis_text = response.strip()
            
            logger.info(f"✅ Image analysis complete: {len(analysis_text)} chars")
            
            return {
                "analysis": analysis_text,
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "prompt": prompt,
                "success": True
            }
            
        except Exception as e:
            logger.error(f"Image analysis failed: {e}", exc_info=True)
            return {
                "analysis": None,
                "model": "meta-llama/llama-4-scout-17b-16e-instruct",
                "prompt": prompt,
                "success": False,
                "error": str(e)
            }


# Global instance
vision_service = VisionService()
