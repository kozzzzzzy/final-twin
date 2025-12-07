"""Logging configuration for TwinSync Spot."""
import logging
import os
import sys


def setup_logging():
    """Configure verbose logging for TwinSync.
    
    Returns:
        Logger: The configured twinsync logger
    """
    level = os.environ.get("LOG_LEVEL", "DEBUG").upper()
    
    # Format with timestamp, level, module, and message
    formatter = logging.Formatter(
        '%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Console handler
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    
    # Root logger
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))
    
    # Clear existing handlers to avoid duplicates
    root.handlers = []
    root.addHandler(handler)
    
    # Set levels for our modules
    logging.getLogger("twinsync").setLevel(logging.DEBUG)
    logging.getLogger("app").setLevel(logging.DEBUG)
    
    # Reduce noise from libraries
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    
    return logging.getLogger("twinsync")
