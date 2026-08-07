from pydantic import BaseModel, ConfigDict
from datetime import datetime

class ConfigurationAdoption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instance_id: str
    active_release_id: str
    pending_release_id: str | None = None
    status: str
    last_heartbeat: datetime
