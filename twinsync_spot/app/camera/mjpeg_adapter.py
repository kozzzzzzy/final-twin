"""MJPEG camera adapter for TwinSync Spot.

Supports HTTP/MJPEG streams and static image URLs.
"""
import asyncio
import logging
import time
from typing import Optional, Tuple, List

import aiohttp

from app.camera.base import BaseCameraAdapter, CameraInfo, CameraError, CameraTestResult

logger = logging.getLogger(__name__)


# Maximum size for MJPEG stream buffer (5MB)
MAX_MJPEG_BUFFER_SIZE = 5 * 1024 * 1024


class MJPEGCamera(BaseCameraAdapter):
    """MJPEG/HTTP camera adapter.
    
    Supports:
    - Static JPEG URLs (snapshot endpoints)
    - MJPEG streams (grabs first frame)
    """
    
    REQUEST_TIMEOUT = 15
    
    def __init__(self):
        self._cameras: List[dict] = []
    
    @property
    def camera_type(self) -> str:
        return "mjpeg"
    
    def set_cameras(self, cameras: List[dict]):
        """Set the list of MJPEG cameras from database.
        
        Args:
            cameras: List of camera dicts with url, name, username, password
        """
        self._cameras = cameras
    
    async def get_cameras(self) -> List[CameraInfo]:
        """Get list of configured MJPEG cameras."""
        return [
            CameraInfo(
                id=f"mjpeg_{cam.get('id', i)}",
                name=cam.get("name", f"HTTP Camera {i}"),
                camera_type="mjpeg",
                url=self._mask_credentials(cam.get("url", "")),
                state="unknown"
            )
            for i, cam in enumerate(self._cameras)
        ]
    
    def _mask_credentials(self, url: str) -> str:
        """Mask credentials in URL for display."""
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            if "@" in rest:
                creds, host_part = rest.rsplit("@", 1)
                return f"{prefix}://***@{host_part}"
        return url
    
    def _build_auth(self, cam: dict) -> Optional[aiohttp.BasicAuth]:
        """Build auth object if credentials provided."""
        username = cam.get("username", "")
        password = cam.get("password", "")
        
        if username:
            return aiohttp.BasicAuth(username, password or "")
        return None
    
    async def get_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Get snapshot from MJPEG/HTTP camera."""
        data, error = await self.get_snapshot_with_error(camera_id)
        return data
    
    async def get_snapshot_with_error(self, camera_id: str) -> Tuple[Optional[bytes], Optional[CameraError]]:
        """Get snapshot with detailed error information."""
        # Find the camera
        cam = None
        for c in self._cameras:
            if f"mjpeg_{c.get('id')}" == camera_id:
                cam = c
                break
        
        if not cam:
            return None, CameraError(
                error_type="not_found",
                message=f"Camera '{camera_id}' not found"
            )
        
        url = cam.get("url", "")
        auth = self._build_auth(cam)
        
        try:
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT)
            
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, auth=auth) as response:
                    if response.status == 401:
                        return None, CameraError(
                            error_type="auth",
                            message="Authentication failed. Check username and password.",
                            status_code=401
                        )
                    
                    if response.status == 404:
                        return None, CameraError(
                            error_type="not_found",
                            message="URL not found. Check the camera URL.",
                            status_code=404
                        )
                    
                    if response.status != 200:
                        return None, CameraError(
                            error_type="unknown",
                            message=f"HTTP error: {response.status}",
                            status_code=response.status
                        )
                    
                    content_type = response.headers.get("Content-Type", "")
                    
                    # Handle MJPEG streams - grab first frame
                    if "multipart" in content_type.lower():
                        data = await self._read_mjpeg_frame(response)
                        if not data:
                            return None, CameraError(
                                error_type="unknown",
                                message="Failed to extract frame from MJPEG stream"
                            )
                        return data, None
                    
                    # Handle static JPEG
                    data = await response.read()
                    
                    if not data:
                        return None, CameraError(
                            error_type="offline",
                            message="Empty response from camera"
                        )
                    
                    # Verify it looks like an image
                    if not self._is_image_data(data):
                        return None, CameraError(
                            error_type="unknown",
                            message="Response does not appear to be an image"
                        )
                    
                    return data, None
                    
        except asyncio.TimeoutError:
            return None, CameraError(
                error_type="timeout",
                message=f"Timeout after {self.REQUEST_TIMEOUT}s"
            )
        except aiohttp.ClientConnectorError as e:
            return None, CameraError(
                error_type="network",
                message=f"Connection error: {str(e)}"
            )
        except aiohttp.ClientError as e:
            return None, CameraError(
                error_type="network",
                message=f"HTTP error: {str(e)}"
            )
        except Exception as e:
            logger.error(f"MJPEG snapshot error: {e}")
            return None, CameraError(
                error_type="unknown",
                message=f"Error: {str(e)}"
            )
    
    async def _read_mjpeg_frame(self, response: aiohttp.ClientResponse) -> Optional[bytes]:
        """Extract a single JPEG frame from an MJPEG stream.
        
        Args:
            response: The aiohttp response object
            
        Returns:
            JPEG image bytes or None
        """
        try:
            # Read chunks looking for JPEG markers
            buffer = b""
            jpeg_start = None
            
            async for chunk in response.content.iter_chunks():
                data, end_of_chunk = chunk
                buffer += data
                
                # Look for JPEG start marker (FFD8)
                if jpeg_start is None:
                    pos = buffer.find(b"\xff\xd8")
                    if pos != -1:
                        jpeg_start = pos
                        buffer = buffer[pos:]
                
                # Look for JPEG end marker (FFD9)
                if jpeg_start is not None:
                    end_pos = buffer.find(b"\xff\xd9")
                    if end_pos != -1:
                        return buffer[:end_pos + 2]
                
                # Don't read too much
                if len(buffer) > MAX_MJPEG_BUFFER_SIZE:
                    break
            
            return None
            
        except Exception as e:
            logger.error(f"Error reading MJPEG stream: {e}")
            return None
    
    def _is_image_data(self, data: bytes) -> bool:
        """Check if data appears to be an image.
        
        Args:
            data: Raw bytes
            
        Returns:
            True if data looks like JPEG or PNG
        """
        if len(data) < 4:
            return False
        
        # JPEG: starts with FFD8FF
        if data[:3] == b"\xff\xd8\xff":
            return True
        
        # PNG: starts with 89504E47
        if data[:4] == b"\x89PNG":
            return True
        
        return False
    
    async def test_connection(self, camera_id: str) -> CameraTestResult:
        """Test connection to MJPEG camera."""
        start = time.time()
        
        data, error = await self.get_snapshot_with_error(camera_id)
        elapsed_ms = (time.time() - start) * 1000
        
        return CameraTestResult(
            success=data is not None,
            error=error,
            response_time_ms=elapsed_ms
        )
    
    async def is_available(self) -> bool:
        """MJPEG adapter is always available."""
        return True
