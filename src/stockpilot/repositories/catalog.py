from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from stockpilot.domain.errors import AlreadyExistsError, ConflictError
from stockpilot.models import Trim, VehicleModel
from stockpilot.repositories.errors import translate_violations


class VehicleModelRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, model_id: int) -> VehicleModel | None:
        return self.session.get(VehicleModel, model_id)

    def search(self, *, limit: int, offset: int) -> Sequence[VehicleModel]:
        query = select(VehicleModel).order_by(VehicleModel.name).limit(limit).offset(offset)
        return self.session.scalars(query).all()

    def count(self) -> int:
        return self.session.scalar(select(func.count()).select_from(VehicleModel)) or 0

    def add(self, model: VehicleModel) -> VehicleModel:
        with translate_violations(self.session, self._duplicate_name(model.name)):
            self.session.add(model)
        return model

    def rename(self, model: VehicleModel, name: str) -> VehicleModel:
        with translate_violations(self.session, self._duplicate_name(name)):
            model.name = name
        return model

    def delete(self, model: VehicleModel) -> None:
        in_use = ConflictError(f"Vehicle model {model.name!r} has trims and cannot be deleted")
        with translate_violations(self.session, foreign_key_error=in_use):
            self.session.delete(model)

    @staticmethod
    def _duplicate_name(name: str) -> dict[str, AlreadyExistsError]:
        return {"uq_vehicle_models_name": AlreadyExistsError("Vehicle model", "name", name)}


class TrimRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, trim_id: int) -> Trim | None:
        return self.session.get(Trim, trim_id)

    def list_for_model(self, model_id: int) -> Sequence[Trim]:
        query = select(Trim).where(Trim.vehicle_model_id == model_id).order_by(Trim.name)
        return self.session.scalars(query).all()

    def add(self, trim: Trim) -> Trim:
        with translate_violations(self.session, self._duplicate_name(trim.name)):
            self.session.add(trim)
        return trim

    def rename(self, trim: Trim, name: str) -> Trim:
        with translate_violations(self.session, self._duplicate_name(name)):
            trim.name = name
        return trim

    def delete(self, trim: Trim) -> None:
        in_use = ConflictError(f"Trim {trim.name!r} has units and cannot be deleted")
        with translate_violations(self.session, foreign_key_error=in_use):
            self.session.delete(trim)

    @staticmethod
    def _duplicate_name(name: str) -> dict[str, AlreadyExistsError]:
        # Trim names are unique within a model, not globally.
        duplicate = AlreadyExistsError("Trim", "name", name)
        return {"uq_trims_vehicle_model_id_name": duplicate}
