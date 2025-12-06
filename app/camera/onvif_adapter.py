"""ONVIF camera adapter for TwinSync Spot.

Provides ONVIF auto-discovery and snapshot capabilities.
Note: Full ONVIF support requires onvif-zeep and wsdiscovery packages.
"""
import asyncio
import logging
import time
from typing import Optional, Tuple, List
from urllib.parse import urlparse

import aiohttp

from app.camera.base import BaseCameraAdapter, CameraInfo, CameraError, CameraTestResult

logger = logging.getLogger(__name__)

# Try to import ONVIF libraries
try:
    from onvif import ONVIFCamera
    ONVIF_AVAILABLE = True
except ImportError:
    ONVIF_AVAILABLE = False
    ONVIFCamera = None

try:
    from wsdiscovery.discovery import ThreadedWSDiscovery
    WSDISCOVERY_AVAILABLE = True
except ImportError:
    WSDISCOVERY_AVAILABLE = False
    ThreadedWSDiscovery = None


class ONVIFCameraAdapter(BaseCameraAdapter):
    """ONVIF camera adapter with auto-discovery.
    
    Supports:
    - ONVIF device discovery on local network
    - ONVIF snapshot endpoint detection
    - Basic authentication
    """
    
    DISCOVERY_TIMEOUT = 10
    
    def __init__(self):
        self._cameras: List[dict] = []
        self._discovered_cameras: List[CameraInfo] = []
    
    @property
    def camera_type(self) -> str:
        return "onvif"
    
    def set_cameras(self, cameras: List[dict]):
        """Set the list of ONVIF cameras from database.
        
        Args:
            cameras: List of camera dicts with host, port, username, password
        """
        self._cameras = cameras
    
    async def get_cameras(self) -> List[CameraInfo]:
        """Get list of configured ONVIF cameras."""
        cameras = []
        
        # Add configured cameras
        for i, cam in enumerate(self._cameras):
            host = cam.get("host", cam.get("url", ""))
            port = cam.get("port", 80)
            cameras.append(CameraInfo(
                id=f"onvif_{cam.get('id', i)}",
                name=cam.get("name", f"ONVIF Camera ({host})"),
                camera_type="onvif",
                url=f"http://{host}:{port}",
                state="unknown"
            ))
        
        # Add any discovered cameras
        cameras.extend(self._discovered_cameras)
        
        return cameras
    
    async def discover_cameras(self, timeout: int = None) -> List[CameraInfo]:
        """Discover ONVIF cameras on the local network.
        
        Args:
            timeout: Discovery timeout in seconds
            
        Returns:
            List of discovered cameras
        """
        if not WSDISCOVERY_AVAILABLE:
            logger.warning("WS-Discovery not available. Install wsdiscovery package.")
            return []
        
        timeout = timeout or self.DISCOVERY_TIMEOUT
        discovered = []
        
        try:
            # Run discovery in thread to not block
            loop = asyncio.get_event_loop()
            
            def do_discovery():
                wsd = ThreadedWSDiscovery()
                wsd.start()
                services = wsd.searchServices(timeout=timeout)
                wsd.stop()
                return services
            
            services = await loop.run_in_executor(None, do_discovery)
            
            for service in services:
                # Filter for ONVIF devices
                scopes = service.getScopes()
                scope_str = " ".join(str(s) for s in scopes)
                
                if "onvif" in scope_str.lower():
                    xaddrs = service.getXAddrs()
                    if xaddrs:
                        for addr in xaddrs:
                            # Extract host from service address
                            try:
                                parsed = urlparse(addr)
                                host = parsed.hostname
                                port = parsed.port or 80
                                
                                # Try to get device name from scopes
                                name = "ONVIF Camera"
                                for scope in scopes:
                                    scope_val = str(scope)
                                    if "name" in scope_val.lower():
                                        name = scope_val.split("/")[-1]
                                        break
                                
                                camera = CameraInfo(
                                    id=f"onvif_discovered_{host}_{port}",
                                    name=f"{name} ({host})",
                                    camera_type="onvif",
                                    url=addr,
                                    state="discovered"
                                )
                                discovered.append(camera)
                            except Exception as e:
                                logger.debug(f"Error parsing ONVIF service: {e}")
            
            self._discovered_cameras = discovered
            logger.info(f"Discovered {len(discovered)} ONVIF cameras")
            
        except Exception as e:
            logger.error(f"ONVIF discovery error: {e}")
        
        return discovered
    
    async def get_snapshot(self, camera_id: str) -> Optional[bytes]:
        """Get snapshot from ONVIF camera."""
        data, error = await self.get_snapshot_with_error(camera_id)
        return data
    
    async def get_snapshot_with_error(self, camera_id: str) -> Tuple[Optional[bytes], Optional[CameraError]]:
        """Get snapshot with detailed error information."""
        if not ONVIF_AVAILABLE:
            return None, CameraError(
                error_type="unknown",
                message="ONVIF support not available. Install onvif-zeep package."
            )
        
        # Find the camera
        cam = None
        for c in self._cameras:
            if f"onvif_{c.get('id')}" == camera_id:
                cam = c
                break
        
        if not cam:
            # Check discovered cameras
            for c in self._discovered_cameras:
                if c.id == camera_id:
                    # For discovered cameras, we need credentials
                    return None, CameraError(
                        error_type="auth",
                        message="Discovered camera needs credentials. Please add it with username/password."
                    )
            
            return None, CameraError(
                error_type="not_found",
                message=f"Camera '{camera_id}' not found"
            )
        
        host = cam.get("host", cam.get("url", ""))
        port = cam.get("port", 80)
        username = cam.get("username", "admin")
        password = cam.get("password", "")
        
        try:
            # Get snapshot URL via ONVIF
            loop = asyncio.get_event_loop()
            
            def get_snapshot_url():
                camera = ONVIFCamera(host, port, username, password)
                media_service = camera.create_media_service()
                profiles = media_service.GetProfiles()
                if not profiles:
                    return None
                profile_token = profiles[0].token
                snapshot_uri = media_service.GetSnapshotUri({'ProfileToken': profile_token})
                return snapshot_uri.Uri
            
            snapshot_url = await loop.run_in_executor(None, get_snapshot_url)
            
            if not snapshot_url:
                return None, CameraError(
                    error_type="unknown",
                    message="Could not get snapshot URL from camera"
                )
            
            # Fetch the snapshot
            auth = aiohttp.BasicAuth(username, password) if username else None
            timeout_config = aiohttp.ClientTimeout(total=15)
            
            async with aiohttp.ClientSession(timeout=timeout_config) as session:
                async with session.get(snapshot_url, auth=auth) as response:
                    if response.status == 401:
                        return None, CameraError(
                            error_type="auth",
                            message="Authentication failed"
                        )
                    
                    if response.status != 200:
                        return None, CameraError(
                            error_type="unknown",
                            message=f"HTTP error: {response.status}"
                        )
                    
                    data = await response.read()
                    if not data:
                        return None, CameraError(
                            error_type="offline",
                            message="Empty response from camera"
                        )
                    
                    return data, None
                    
        except Exception as e:
            error_str = str(e)
            logger.error(f"ONVIF snapshot error: {e}")
            
            if "401" in error_str or "auth" in error_str.lower():
                return None, CameraError(
                    error_type="auth",
                    message="Authentication failed. Check username and password."
                )
            elif "timeout" in error_str.lower():
                return None, CameraError(
                    error_type="timeout",
                    message="Connection timed out"
                )
            else:
                return None, CameraError(
                    error_type="unknown",
                    message=f"Error: {error_str[:200]}"
                )
    
    async def test_connection(self, camera_id: str) -> CameraTestResult:
        """Test connection to ONVIF camera."""
        start = time.time()
        
        data, error = await self.get_snapshot_with_error(camera_id)
        elapsed_ms = (time.time() - start) * 1000
        
        return CameraTestResult(
            success=data is not None,
            error=error,
            response_time_ms=elapsed_ms
        )
    
    async def is_available(self) -> bool:
        """Check if ONVIF libraries are installed."""
        return ONVIF_AVAILABLE
