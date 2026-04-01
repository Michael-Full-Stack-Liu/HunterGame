from .builtins import get_job_hunter_tools
from .actuators import get_actuator_tools

def get_all_tools():
    """Combines all available tools for the agent."""
    return get_job_hunter_tools() + get_actuator_tools()
