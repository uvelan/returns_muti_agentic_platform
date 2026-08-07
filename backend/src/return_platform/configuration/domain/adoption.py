from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import List

class ConfigurationAdoption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instance_id: str
    adopted_release_id: str
    adopted_epoch: int
    adopted_at: datetime
    pending_release_id: str | None = None
    requires_restart: bool = False
    draining_epochs: List[int] = []
    heartbeat_at: datetime
