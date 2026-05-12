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
    
    print("""
╔═══════════════════════════════════════════════════════════════╗
║                    AuroraCoder - Web Interface                  ║
║              Your Intelligent Coding Companion                  ║
╠═══════════════════════════════════════════════════════════════╣
║                                                                 ║
║  Backend API:  http://localhost:8080                           ║
║  API Docs:     http://localhost:8080/docs                      ║
║                                                                 ║
║  To start the frontend:                                        ║
║    cd frontend                                                  ║
║    npm install                                                  ║
║    npm run dev                                                  ║
║                                                                 ║
║  Frontend will be available at: http://localhost:3000          ║
║                                                                 ║
╚═══════════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        app, 
        host="0.0.0.0", 
        port=8080, 
        log_level="info",
        reload=False
    )
