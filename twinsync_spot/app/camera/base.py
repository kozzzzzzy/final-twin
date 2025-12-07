"""Base camera interface for TwinSync Spot.

Defines the abstract interface that all camera adapters must implement.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Tuple, List


@dataclass
class CameraInfo:
    """Information about a camera."""
    id: str  # Unique identifier
    name: str
    camera_type: str  # "ha", "rtsp", "onvif", "mjpeg"
    url: Optional[str] = None
    state: str = "unknown"
    
    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "camera_type": self.camera_type,
            "url": self.url,
            "state": self.state,
        }


@dataclass
class CameraError:
    """Error information for camera operations."""
    error_type: str  # "auth", "timeout", "offline", "not_found", "network", "unknown"
    message: str
    status_code: Optional[int] = None
    
    def __str__(self) -> str:
        return self.message


@dataclass 
class CameraTestResult:
    """Result of testing a camera connection."""
    success: bool
    error: Optional[CameraError] = None
    response_time_ms: Optional[float] = None


class BaseCameraAdapter(ABC):
    """Abstract base class for camera adapters.
    
    All camera adapters (HA, RTSP, ONVIF, MJPEG) must implement this interface.
    """
    
    @property
    @abstractmethod
    def camera_type(self) -> str:
        """Return the camera type identifier."""
        pass
    
    @abstractmethod
    async def get_cameras(self) -> List[CameraInfo]:
        """Get list of available cameras.
        
        Returns:
            List of CameraInfo objects for each available camera
        """
        pass
    
    @abstractmethod
    async def get_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Get a snapshot from a camera.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            Image bytes on success, None on failure
        """
        pass
    
    @abstractmethod
    async def get_snapshot_with_error(self, camera_id: str) -> Tuple[Optional[bytes], Optional[CameraError]]:
        """Get a snapshot with detailed error information.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            Tuple of (image_bytes, error). On success: (bytes, None). On failure: (None, error)
        """
        pass
    
    @abstractmethod
    async def test_connection(self, camera_id: str) -> CameraTestResult:
        """Test connection to a camera.
        
        Args:
            camera_id: The camera identifier
            
        Returns:
            CameraTestResult with success status, timing, and any error info
        """
        pass
    
    async def is_available(self) -> bool:
        """Check if this adapter is available/configured.
        
        Override in subclasses that need specific configuration.
        
        Returns:
            True if the adapter can be used
        """
        return True
