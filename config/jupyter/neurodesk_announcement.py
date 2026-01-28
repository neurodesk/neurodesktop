"""
JupyterLab announcement for Neurodesk donations
This uses JupyterLab's built-in announcement/notification system
"""

from jupyter_server.base.handlers import JupyterHandler
import json


class AnnouncementHandler(JupyterHandler):
    """Handler to provide announcement data"""
    
    def get(self):
        """Return announcement as JSON"""
        announcement = {
            "message": "💜 Support Neurodesk! Help us maintain this free platform by <a href='https://donations.uq.edu.au/EAINNEUR' target='_blank'>donating to our infrastructure costs</a>.",
            "type": "info",
            "modified": "2026-01-28T00:00:00Z",  # Update timestamp to show
            "link": "https://donations.uq.edu.au/EAINNEUR"
        }
        self.set_header("Content-Type", "application/json")
        self.finish(json.dumps(announcement))


def _jupyter_server_extension_points():
    return [{
        "module": "neurodesk_announcement"
    }]


def _load_jupyter_server_extension(serverapp):
    """Load the extension"""
    web_app = serverapp.web_app
    host_pattern = ".*$"
    route_pattern = "/lab/api/news"
    
    handlers = [(route_pattern, AnnouncementHandler)]
    web_app.add_handlers(host_pattern, handlers)
    serverapp.log.info("Neurodesk announcement extension loaded")
