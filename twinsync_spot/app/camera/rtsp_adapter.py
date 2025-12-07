"""RTSP camera adapter for TwinSync Spot.

Supports RTSP streams from cameras like Tapo, Reolink, Hikvision, etc.
"""
import asyncio
import logging
import subprocess
import tempfile
import time
from typing import Optional, Tuple, List

from app.camera.base import BaseCameraAdapter, CameraInfo, CameraError, CameraTestResult

logger = logging.getLogger(__name__)


class RTSPCamera(BaseCameraAdapter):
    """RTSP camera adapter using FFmpeg for snapshots."""
    
    # Timeout for FFmpeg operations
    FFMPEG_TIMEOUT = 15
    
    # Cache FFmpeg availability check
    _ffmpeg_available: Optional[bool] = None
    
    def __init__(self):
        # RTSP cameras are stored in the database
        self._cameras: List[dict] = []
    
    @property
    def camera_type(self) -> str:
        return "rtsp"
    
    def set_cameras(self, cameras: List[dict]):
        """Set the list of RTSP cameras from database.
        
        Args:
            cameras: List of camera dicts with url, name, username, password
        """
        self._cameras = cameras
    
    async def get_cameras(self) -> List[CameraInfo]:
        """Get list of configured RTSP cameras."""
        return [
            CameraInfo(
                id=f"rtsp_{cam.get('id', i)}",
                name=cam.get("name", f"RTSP Camera {i}"),
                camera_type="rtsp",
                url=self._mask_credentials(cam.get("url", "")),
                state="unknown"
            )
            for i, cam in enumerate(self._cameras)
        ]
    
    def _mask_credentials(self, url: str) -> str:
        """Mask credentials in RTSP URL for display."""
        # rtsp://user:pass@host:port/path -> rtsp://***@host:port/path
        if "@" in url and "://" in url:
            prefix, rest = url.split("://", 1)
            if "@" in rest:
                creds, host_part = rest.rsplit("@", 1)
                return f"{prefix}://***@{host_part}"
        return url
    
    def _build_rtsp_url(self, cam: dict) -> str:
        """Build full RTSP URL with credentials.
        
        Args:
            cam: Camera dict with url, username, password
            
        Returns:
            Full RTSP URL
        """
        url = cam.get("url", "")
        username = cam.get("username", "")
        password = cam.get("password", "")
        
        if username and password and "://" in url and "@" not in url:
            # Insert credentials into URL
            prefix, rest = url.split("://", 1)
            return f"{prefix}://{username}:{password}@{rest}"
        
        return url
    
    async def get_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Get snapshot from RTSP camera using FFmpeg."""
        data, error = await self.get_snapshot_with_error(camera_id)
        return data
    
    async def get_snapshot_with_error(self, camera_id: str) -> Tuple[Optional[bytes], Optional[CameraError]]:
        """Get snapshot with detailed error information."""
        # Find the camera
        cam = None
        for c in self._cameras:
            if f"rtsp_{c.get('id')}" == camera_id:
                cam = c
                break
        
        if not cam:
            return None, CameraError(
                error_type="not_found",
                message=f"Camera '{camera_id}' not found"
            )
        
        rtsp_url = self._build_rtsp_url(cam)
        
        try:
            # Use FFmpeg to capture a single frame
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=True) as tmp:
                cmd = [
                    "ffmpeg",
                    "-y",  # Overwrite output
                    "-rtsp_transport", "tcp",  # Use TCP for reliability
                    "-i", rtsp_url,
                    "-frames:v", "1",  # Just one frame
                    "-q:v", "2",  # Quality (2 is high)
                    "-f", "image2",
                    tmp.name
                ]
                
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE
                )
                
                try:
                    _, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=self.FFMPEG_TIMEOUT
                    )
                except asyncio.TimeoutError:
                    process.kill()
                    return None, CameraError(
                        error_type="timeout",
                        message=f"Timeout capturing from RTSP stream after {self.FFMPEG_TIMEOUT}s"
                    )
                
                if process.returncode != 0:
                    error_text = stderr.decode("utf-8", errors="replace")[:200] if stderr else "Unknown error"
                    
                    if "401" in error_text or "Unauthorized" in error_text:
                        return None, CameraError(
                            error_type="auth",
                            message="Authentication failed. Check username and password."
                        )
                    elif "Connection refused" in error_text:
                        return None, CameraError(
                            error_type="network",
                            message="Connection refused. Check camera IP and port."
                        )
                    else:
                        return None, CameraError(
                            error_type="unknown",
                            message=f"FFmpeg error: {error_text}"
                        )
                
                # Read the captured image
                with open(tmp.name, "rb") as f:
                    data = f.read()
                
                if not data:
                    return None, CameraError(
                        error_type="offline",
                        message="Empty image returned from camera"
                    )
                
                return data, None
                
        except FileNotFoundError:
            return None, CameraError(
                error_type="unknown",
                message="FFmpeg not installed. RTSP support requires FFmpeg."
            )
        except Exception as e:
            logger.error(f"RTSP snapshot error: {e}")
            return None, CameraError(
                error_type="unknown",
                message=f"Error: {str(e)}"
            )
    
    async def test_connection(self, camera_id: str) -> CameraTestResult:
        """Test connection to RTSP camera."""
        start = time.time()
        
        data, error = await self.get_snapshot_with_error(camera_id)
        elapsed_ms = (time.time() - start) * 1000
        
        return CameraTestResult(
            success=data is not None,
            error=error,
            response_time_ms=elapsed_ms
        )
    
    async def is_available(self) -> bool:
        """Check if FFmpeg is installed (cached)."""
        # Return cached result if available
        if RTSPCamera._ffmpeg_available is not None:
            return RTSPCamera._ffmpeg_available
        
        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg", "-version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL
            )
            await process.wait()
            RTSPCamera._ffmpeg_available = process.returncode == 0
        except FileNotFoundError:
            RTSPCamera._ffmpeg_available = False
        
        return RTSPCamera._ffmpeg_available
