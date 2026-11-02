from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from relay.application.agent_ports import AgentUnitOfWork
from relay.application.errors import Conflict, NotFound
from relay.domain.devices import Device, DeviceStatus


@dataclass(frozen=True, slots=True)
class CreateDevice:
    external_device_id: str
    name: str
    platform: str
    app_version: str | None = None
    status: DeviceStatus = DeviceStatus.PENDING


@dataclass(frozen=True, slots=True)
class UpdateDevice:
    name: str | None = None
    status: DeviceStatus | None = None
    app_version: str | None = None
    app_version_supplied: bool = False


class DeviceService:
    def __init__(self, uow: AgentUnitOfWork) -> None:
        self.uow = uow

    async def _unique_name(self, name: str, exclude: UUID | None = None) -> None:
        # One active record per phone: a second device with the same name is
        # almost always the same phone added twice.
        if await self.uow.devices.name_in_use(name, exclude):
            raise Conflict("A device with this name already exists.")

    async def create(self, command: CreateDevice) -> Device:
        await self._unique_name(command.name)
        now = datetime.now(UTC)
        device = Device(
            id=uuid4(),
            external_device_id=command.external_device_id,
            name=command.name,
            platform=command.platform,
            app_version=command.app_version,
            status=command.status,
            created_at=now,
            updated_at=now,
        )
        await self.uow.devices.add(device)
        await self.uow.commit()
        return device

    async def get(self, device_id: UUID) -> Device:
        device = await self.uow.devices.get(device_id)
        if device is None:
            raise NotFound("Device not found.")
        return device

    async def list(self, *, limit: int, offset: int) -> list[Device]:
        return await self.uow.devices.list(limit=limit, offset=offset)

    async def update(self, device_id: UUID, command: UpdateDevice) -> Device:
        # Serialize read-modify-write on PostgreSQL so disjoint PATCHes are retained.
        device = await self.uow.devices.get(device_id, for_update=True)
        if device is None:
            raise NotFound("Device not found.")
        reactivating = (
            device.status == DeviceStatus.DISABLED
            and command.status not in (None, DeviceStatus.DISABLED)
        )
        if command.name is not None or reactivating:
            if (command.status or device.status) != DeviceStatus.DISABLED:
                await self._unique_name(command.name or device.name, device.id)
        updated = replace(
            device,
            name=command.name if command.name is not None else device.name,
            status=command.status if command.status is not None else device.status,
            app_version=(
                command.app_version
                if command.app_version_supplied
                else device.app_version
            ),
            updated_at=datetime.now(UTC),
        )
        if updated.status == DeviceStatus.DISABLED:
            await self.uow.agents.revoke(updated.id, updated.updated_at)
        await self.uow.devices.save(updated)
        await self.uow.commit()
        return updated
