from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConfigurationAdoption(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    instance_id: str
    adopted_release_id: str
    adopted_epoch: int
    adopted_at: datetime
    pending_release_id: str | None = None
    requires_restart: bool = False
    draining_epochs: list[int] = []
    heartbeat_at: datetime
