"""Home Assistant camera adapter."""
import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional, Tuple

import aiohttp

from app.core.models import Camera
from app.core.config import ConfigManager


logger = logging.getLogger(__name__)


@dataclass
class SnapshotError:
    """Detailed error information for snapshot failures."""
    error_type: str  # "auth", "timeout", "offline", "not_found", "server_error", "network", "unknown"
    message: str
    status_code: Optional[int] = None
    response_body: Optional[str] = None

    def __str__(self) -> str:
        return self.message


@dataclass
class ConnectionTestResult:
    """Result of a camera connection test."""
    success: bool
    error: Optional[SnapshotError] = None
    response_time_ms: Optional[float] = None


class HACamera:
    """Home Assistant camera adapter."""
    
    # Retry configuration
    MAX_RETRIES = 3
    BASE_BACKOFF_SECONDS = 1.0
    REQUEST_TIMEOUT_SECONDS = 30
    
    # Token file path for file-based fallback (legacy)
    TOKEN_FILE_PATH = "/data/.ha_token"
    
    def __init__(self, db_path: str = "/data/twinsync.db"):
        self.config = ConfigManager(db_path)
        # Cache credentials - will be populated on first use
        self._cached_url: Optional[str] = None
        self._cached_token: Optional[str] = None
        self._credentials_loaded = False

    async def _load_credentials(self):
        """Load credentials from ConfigManager, with fallbacks for legacy setups."""
        if self._credentials_loaded:
            return
        
        # Try ConfigManager first (new way)
        self._cached_url = await self.config.get_ha_url()
        self._cached_token = await self.config.get_ha_token()
        
        # Fallback to environment for backwards compatibility
        if not self._cached_url:
            # Check if running as HA add-on
            if os.environ.get("SUPERVISOR_TOKEN"):
                self._cached_url = "http://supervisor/core"
            else:
                self._cached_url = os.environ.get("HA_BASE_URL", "")
        
        if not self._cached_token:
            # Try SUPERVISOR_TOKEN first (standard for HA add-ons)
            self._cached_token = os.environ.get("SUPERVISOR_TOKEN", "")
            
            # Try HASSIO_TOKEN (legacy/older setups)
            if not self._cached_token:
                self._cached_token = os.environ.get("HASSIO_TOKEN", "")
            
            # Try reading from file (user's manual fix - legacy)
            if not self._cached_token and os.path.exists(self.TOKEN_FILE_PATH):
                try:
                    with open(self.TOKEN_FILE_PATH, "r") as f:
                        token = f.read().strip()
                        if token:
                            logger.info(f"Using HA token from {self.TOKEN_FILE_PATH} file")
                            self._cached_token = token
                except (FileNotFoundError, PermissionError, IOError) as e:
                    logger.warning(f"Failed to read token file: {e}")
        
        self._credentials_loaded = True
        
        if not self._cached_token:
            logger.warning("No Home Assistant token found - cameras will not work")
    
    async def _get_credentials(self) -> Tuple[Optional[str], Optional[str]]:
        """Get HA URL and token."""
        await self._load_credentials()
        return self._cached_url, self._cached_token
    
    def invalidate_credentials_cache(self):
        """Invalidate the credentials cache (call after settings are updated)."""
        self._credentials_loaded = False
        self._cached_url = None
        self._cached_token = None
    
    async def get_cameras(self) -> list[Camera]:
        """Get list of camera entities from Home Assistant."""
        ha_url, ha_token = await self._get_credentials()
        
        if not ha_token:
            return []
        
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"Authorization": f"Bearer {ha_token}"}
                url = f"{ha_url}/api/states"
                
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        return []
                    
                    states = await response.json()
            
            cameras = []
            for state in states:
                entity_id = state.get("entity_id", "")
                if entity_id.startswith("camera."):
                    cameras.append(Camera(
                        entity_id=entity_id,
                        name=state.get("attributes", {}).get("friendly_name", entity_id),
                        state=state.get("state", "unknown")
                    ))
            
            return cameras
            
        except Exception as e:
            logger.error(f"Error fetching cameras: {e}")
            return []
    
    def _parse_error_response(self, status: int, body: str, entity_id: str) -> SnapshotError:
        """Parse HTTP response into a specific error type."""
        if status == 401:
            return SnapshotError(
                error_type="auth",
                message="Authentication failed. Check your Home Assistant access token.",
                status_code=status,
                response_body=body[:500] if body else None
            )
        elif status == 403:
            return SnapshotError(
                error_type="auth",
                message="Access denied. The token may lack camera permissions.",
                status_code=status,
                response_body=body[:500] if body else None
            )
        elif status == 404:
            return SnapshotError(
                error_type="not_found",
                message=f"Camera '{entity_id}' not found. It may have been removed or renamed.",
                status_code=status,
                response_body=body[:500] if body else None
            )
        elif status == 502 or status == 503:
            return SnapshotError(
                error_type="offline",
                message=f"Camera '{entity_id}' appears to be offline or unavailable (status {status}).",
                status_code=status,
                response_body=body[:500] if body else None
            )
        elif status >= 500:
            return SnapshotError(
                error_type="server_error",
                message=f"Home Assistant server error (status {status}). Try again later.",
                status_code=status,
                response_body=body[:500] if body else None
            )
        else:
            return SnapshotError(
                error_type="unknown",
                message=f"Unexpected response from camera (status {status}).",
                status_code=status,
                response_body=body[:500] if body else None
            )
    
    async def _get_snapshot_once(self, entity_id: str, session: aiohttp.ClientSession) -> Tuple[Optional[bytes], Optional[SnapshotError]]:
        """Attempt to get a snapshot once. Returns (bytes, None) on success or (None, error) on failure."""
        ha_url, ha_token = await self._get_credentials()
        
        if not ha_token:
            return None, SnapshotError(
                error_type="auth",
                message="No Home Assistant token configured. Please configure in Settings."
            )
        
        headers = {"Authorization": f"Bearer {ha_token}"}
        url = f"{ha_url}/api/camera_proxy/{entity_id}"
        
        try:
            timeout = aiohttp.ClientTimeout(total=self.REQUEST_TIMEOUT_SECONDS)
            async with session.get(url, headers=headers, timeout=timeout) as response:
                if response.status == 200:
                    data = await response.read()
                    if data and len(data) > 0:
                        return data, None
                    else:
                        return None, SnapshotError(
                            error_type="offline",
                            message=f"Camera '{entity_id}' returned empty image data.",
                            status_code=200
                        )
                
                # Non-200 response
                body = await response.text()
                error = self._parse_error_response(response.status, body, entity_id)
                # Log only status code to avoid exposing sensitive info in logs
                logger.warning(f"Snapshot failed for {entity_id}: status={response.status}")
                return None, error
                
        except asyncio.TimeoutError:
            return None, SnapshotError(
                error_type="timeout",
                message=f"Camera '{entity_id}' timed out after {self.REQUEST_TIMEOUT_SECONDS}s. The camera may be slow or unresponsive."
            )
        except aiohttp.ClientConnectorError as e:
            return None, SnapshotError(
                error_type="network",
                message=f"Cannot connect to Home Assistant: {str(e)}"
            )
        except aiohttp.ClientError as e:
            return None, SnapshotError(
                error_type="network",
                message=f"Network error getting snapshot: {str(e)}"
            )
    
    async def get_snapshot(self, entity_id: str) -> Optional[bytes]:
        """Get a snapshot from a camera with retry logic.
        
        Attempts up to MAX_RETRIES times with exponential backoff.
        Returns image bytes on success, None on failure.
        """
        ha_url, ha_token = await self._get_credentials()
        
        if not ha_token:
            logger.error("No Home Assistant token configured - cannot access cameras")
            return None
        
        last_error: Optional[SnapshotError] = None
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(self.MAX_RETRIES):
                data, error = await self._get_snapshot_once(entity_id, session)
                
                if data is not None:
                    if attempt > 0:
                        logger.info(f"Snapshot succeeded for {entity_id} on attempt {attempt + 1}")
                    return data
                
                last_error = error
                
                # Don't retry for certain error types
                if error and error.error_type in ("auth", "not_found"):
                    logger.error(f"Snapshot failed for {entity_id} (not retrying): {error}")
                    break
                
                # Exponential backoff
                if attempt < self.MAX_RETRIES - 1:
                    backoff = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(f"Snapshot attempt {attempt + 1} failed for {entity_id}: {error}. Retrying in {backoff}s...")
                    await asyncio.sleep(backoff)
        
        if last_error:
            logger.error(f"All snapshot attempts failed for {entity_id}: {last_error}")
        
        return None
    
    async def get_snapshot_with_error(self, entity_id: str) -> Tuple[Optional[bytes], Optional[SnapshotError]]:
        """Get a snapshot with detailed error information.
        
        Returns (bytes, None) on success or (None, SnapshotError) on failure.
        Use this when you need to display specific error messages to users.
        """
        ha_url, ha_token = await self._get_credentials()
        
        if not ha_token:
            return None, SnapshotError(
                error_type="auth",
                message="No Home Assistant token configured. Please configure in Settings."
            )
        
        last_error: Optional[SnapshotError] = None
        
        async with aiohttp.ClientSession() as session:
            for attempt in range(self.MAX_RETRIES):
                data, error = await self._get_snapshot_once(entity_id, session)
                
                if data is not None:
                    return data, None
                
                last_error = error
                
                # Don't retry for certain error types
                if error and error.error_type in ("auth", "not_found"):
                    break
                
                # Exponential backoff
                if attempt < self.MAX_RETRIES - 1:
                    backoff = self.BASE_BACKOFF_SECONDS * (2 ** attempt)
                    await asyncio.sleep(backoff)
        
        return None, last_error
    
    async def test_connection(self, entity_id: str = None) -> ConnectionTestResult:
        """Test camera connection and return detailed diagnostics.
        
        Can be called from UI to diagnose camera issues.
        Returns success status, timing info, and any error details.
        
        If entity_id is None, tests just the HA connection.
        """
        ha_url, ha_token = await self._get_credentials()
        
        if not ha_token:
            return ConnectionTestResult(
                success=False,
                error=SnapshotError(
                    error_type="auth",
                    message="No Home Assistant token configured. Please configure in Settings."
                )
            )
        
        start_time = time.time()
        
        # If no entity_id, just test HA connection
        if not entity_id:
            try:
                async with aiohttp.ClientSession() as session:
                    headers = {"Authorization": f"Bearer {ha_token}"}
                    url = f"{ha_url}/api/"
                    timeout = aiohttp.ClientTimeout(total=10)
                    async with session.get(url, headers=headers, timeout=timeout) as response:
                        elapsed_ms = (time.time() - start_time) * 1000
                        if response.status == 200:
                            return ConnectionTestResult(
                                success=True,
                                response_time_ms=elapsed_ms
                            )
                        else:
                            return ConnectionTestResult(
                                success=False,
                                error=self._parse_error_response(response.status, "", "HA API"),
                                response_time_ms=elapsed_ms
                            )
            except Exception as e:
                elapsed_ms = (time.time() - start_time) * 1000
                return ConnectionTestResult(
                    success=False,
                    error=SnapshotError(
                        error_type="network",
                        message=f"Cannot connect to Home Assistant: {str(e)}"
                    ),
                    response_time_ms=elapsed_ms
                )
        
        # Test specific camera
        async with aiohttp.ClientSession() as session:
            data, error = await self._get_snapshot_once(entity_id, session)
            
            elapsed_ms = (time.time() - start_time) * 1000
            
            if data is not None:
                return ConnectionTestResult(
                    success=True,
                    response_time_ms=elapsed_ms
                )
            else:
                return ConnectionTestResult(
                    success=False,
                    error=error,
                    response_time_ms=elapsed_ms
                )
    
    async def test_camera(self, entity_id: str) -> bool:
        """Test if a camera is accessible (simple boolean check)."""
        result = await self.test_connection(entity_id)
        return result.success
