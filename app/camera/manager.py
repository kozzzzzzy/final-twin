"""Unified Camera Manager for TwinSync Spot.

Provides a single interface to access cameras from all sources:
- Home Assistant cameras
- RTSP cameras (Tapo, Reolink, etc.)
- ONVIF cameras with auto-discovery
- MJPEG/HTTP streams
"""
import logging
from typing import Optional, Tuple, List, Dict

from app.camera.base import CameraInfo, CameraError, CameraTestResult
from app.camera.ha_adapter import HACamera
from app.camera.rtsp_adapter import RTSPCamera
from app.camera.mjpeg_adapter import MJPEGCamera
from app.camera.onvif_adapter import ONVIFCameraAdapter

logger = logging.getLogger(__name__)


class CameraManager:
    """Unified camera manager that aggregates all camera sources.
    
    Provides a single interface for:
    - Listing all available cameras
    - Getting snapshots from any camera type
    - Testing camera connections
    - Camera discovery (ONVIF)
    """
    
    def __init__(self, db_path: str = "/data/twinsync.db"):
        self.db_path = db_path
        
        # Initialize adapters
        self.ha_camera = HACamera(db_path)
        self.rtsp_camera = RTSPCamera()
        self.mjpeg_camera = MJPEGCamera()
        self.onvif_camera = ONVIFCameraAdapter()
        
        # Map camera type prefixes to adapters
        self._adapters = {
            "camera.": self.ha_camera,  # HA cameras start with "camera."
            "rtsp_": self.rtsp_camera,
            "mjpeg_": self.mjpeg_camera,
            "onvif_": self.onvif_camera,
        }
    
    def _get_adapter_for_camera(self, camera_id: str):
        """Get the appropriate adapter for a camera ID.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            The appropriate camera adapter, or None
        """
        for prefix, adapter in self._adapters.items():
            if camera_id.startswith(prefix):
                return adapter
        
        # Default to HA camera for backwards compatibility
        return self.ha_camera
    
    async def get_all_cameras(self) -> List[CameraInfo]:
        """Get list of all cameras from all sources.
        
        Returns:
            Combined list of cameras from all adapters
        """
        all_cameras = []
        
        # Get HA cameras
        try:
            ha_cameras = await self.ha_camera.get_cameras()
            for cam in ha_cameras:
                all_cameras.append(CameraInfo(
                    id=cam.entity_id,
                    name=cam.name,
                    camera_type="ha",
                    state=cam.state
                ))
        except Exception as e:
            logger.error(f"Error getting HA cameras: {e}")
        
        # Get RTSP cameras
        try:
            rtsp_cameras = await self.rtsp_camera.get_cameras()
            all_cameras.extend(rtsp_cameras)
        except Exception as e:
            logger.error(f"Error getting RTSP cameras: {e}")
        
        # Get MJPEG cameras
        try:
            mjpeg_cameras = await self.mjpeg_camera.get_cameras()
            all_cameras.extend(mjpeg_cameras)
        except Exception as e:
            logger.error(f"Error getting MJPEG cameras: {e}")
        
        # Get ONVIF cameras
        try:
            onvif_cameras = await self.onvif_camera.get_cameras()
            all_cameras.extend(onvif_cameras)
        except Exception as e:
            logger.error(f"Error getting ONVIF cameras: {e}")
        
        return all_cameras
    
    async def get_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Get snapshot from any camera.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            Image bytes on success, None on failure
        """
        adapter = self._get_adapter_for_camera(camera_id)
        
        if camera_id.startswith("camera."):
            # HA cameras use entity_id directly
            return await adapter.get_snapshot(camera_id)
        else:
            return await adapter.get_snapshot(camera_id)
    
    async def get_snapshot_with_error(self, camera_id: str) -> Tuple[Optional[bytes], Optional[CameraError]]:
        """Get snapshot with detailed error information.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            Tuple of (image_bytes, error)
        """
        adapter = self._get_adapter_for_camera(camera_id)
        
        if camera_id.startswith("camera."):
            # HA adapter returns SnapshotError, convert to CameraError
            data, ha_error = await adapter.get_snapshot_with_error(camera_id)
            if ha_error:
                return None, CameraError(
                    error_type=ha_error.error_type,
                    message=ha_error.message,
                    status_code=ha_error.status_code
                )
            return data, None
        else:
            return await adapter.get_snapshot_with_error(camera_id)
    
    async def test_connection(self, camera_id: str) -> CameraTestResult:
        """Test connection to any camera.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            CameraTestResult with success status and timing info
        """
        adapter = self._get_adapter_for_camera(camera_id)
        
        if camera_id.startswith("camera."):
            # HA adapter returns ConnectionTestResult, convert to CameraTestResult
            result = await adapter.test_connection(camera_id)
            error = None
            if result.error:
                error = CameraError(
                    error_type=result.error.error_type,
                    message=result.error.message,
                    status_code=result.error.status_code
                )
            return CameraTestResult(
                success=result.success,
                error=error,
                response_time_ms=result.response_time_ms
            )
        else:
            return await adapter.test_connection(camera_id)
    
    async def discover_onvif_cameras(self, timeout: int = 10) -> List[CameraInfo]:
        """Discover ONVIF cameras on the local network.
        
        Args:
            timeout: Discovery timeout in seconds
            
        Returns:
            List of discovered cameras
        """
        return await self.onvif_camera.discover_cameras(timeout)
    
    def load_custom_cameras(self, cameras: List[Dict]):
        """Load custom cameras from database.
        
        Args:
            cameras: List of camera dicts with type, url, name, credentials
        """
        rtsp_cameras = []
        mjpeg_cameras = []
        onvif_cameras = []
        
        for cam in cameras:
            cam_type = cam.get("camera_type", "mjpeg")
            
            if cam_type == "rtsp":
                rtsp_cameras.append(cam)
            elif cam_type == "mjpeg":
                mjpeg_cameras.append(cam)
            elif cam_type == "onvif":
                onvif_cameras.append(cam)
        
        self.rtsp_camera.set_cameras(rtsp_cameras)
        self.mjpeg_camera.set_cameras(mjpeg_cameras)
        self.onvif_camera.set_cameras(onvif_cameras)
        
        logger.info(f"Loaded custom cameras: {len(rtsp_cameras)} RTSP, {len(mjpeg_cameras)} MJPEG, {len(onvif_cameras)} ONVIF")
    
    async def get_available_adapters(self) -> Dict[str, bool]:
        """Check which camera adapters are available.
        
        Returns:
            Dict of adapter_name: is_available
        """
        return {
            "ha": True,  # HA adapter is always available
            "rtsp": await self.rtsp_camera.is_available(),
            "mjpeg": await self.mjpeg_camera.is_available(),
            "onvif": await self.onvif_camera.is_available(),
        }
    
    def invalidate_ha_credentials(self):
        """Invalidate cached HA credentials (call after settings change)."""
        self.ha_camera.invalidate_credentials_cache()
