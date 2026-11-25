from collections.abc import Sequence

from sqlalchemy.orm import Session

from stockpilot.domain.errors import NotFoundError
from stockpilot.models import Trim, VehicleModel
from stockpilot.repositories.catalog import TrimRepository, VehicleModelRepository
from stockpilot.schemas.catalog import TrimCreate, TrimUpdate, VehicleModelCreate


class CatalogService:
    """Vehicle models and their trims. Each public method is one transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.models = VehicleModelRepository(session)
        self.trims = TrimRepository(session)

    # --- Vehicle models -------------------------------------------------------

    def get_model(self, model_id: int) -> VehicleModel:
        model = self.models.get(model_id)
        if model is None:
            raise NotFoundError("Vehicle model", model_id)
        return model

    def search_models(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sequence[VehicleModel], int]:
        return self.models.search(limit=limit, offset=offset), self.models.count()

    def create_model(self, data: VehicleModelCreate) -> VehicleModel:
        model = self.models.add(VehicleModel(name=data.name))
        self.session.commit()
        return model

    def rename_model(self, model_id: int, name: str) -> VehicleModel:
        model = self.models.rename(self.get_model(model_id), name)
        self.session.commit()
        return model

    def delete_model(self, model_id: int) -> None:
        self.models.delete(self.get_model(model_id))
        self.session.commit()

    # --- Trims ----------------------------------------------------------------

    def get_trim(self, trim_id: int) -> Trim:
        trim = self.trims.get(trim_id)
        if trim is None:
            raise NotFoundError("Trim", trim_id)
        return trim

    def list_trims(self, model_id: int) -> Sequence[Trim]:
        # 404 for an unknown model, instead of an empty list that hides a wrong id.
        self.get_model(model_id)
        return self.trims.list_for_model(model_id)

    def create_trim(self, model_id: int, data: TrimCreate) -> Trim:
        model = self.get_model(model_id)
        trim = self.trims.add(Trim(vehicle_model=model, **data.model_dump()))
        self.session.commit()
        return trim

    def update_trim(self, trim_id: int, data: TrimUpdate) -> Trim:
        changes = data.model_dump(exclude_unset=True)
        trim = self.trims.update(self.get_trim(trim_id), changes)
        self.session.commit()
        return trim

    def delete_trim(self, trim_id: int) -> None:
        self.trims.delete(self.get_trim(trim_id))
        self.session.commit()
