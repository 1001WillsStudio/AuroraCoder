#!/usr/bin/env python
"""
Run the ThinkWithTool Web Interface

This script starts both the FastAPI backend and provides instructions
for running the React frontend.
"""

import sys
import os
import logging

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

if __name__ == "__main__":
    import uvicorn
    from src.web_api import app
    
    logger = logging.getLogger(__name__)
    logger.info("AuroraCoder backend starting on http://0.0.0.0:8080")
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8080, 
        log_level="warning",
        reload=False
    )
