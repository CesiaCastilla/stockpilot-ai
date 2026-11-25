"""Catalog routes.

Shallow nesting: a trim is created and listed through its model
(/vehicle-models/{id}/trims) because it cannot exist without one, but once it
has its own id it is addressed directly (/trims/{id}).
"""

from typing import Any

from fastapi import APIRouter, Response, status

from stockpilot.api.dependencies import CatalogServiceDep
from stockpilot.api.params import Limit, Offset
from stockpilot.schemas.catalog import (
    TrimCreate,
    TrimRead,
    TrimUpdate,
    VehicleModelCreate,
    VehicleModelRead,
    VehicleModelUpdate,
)
from stockpilot.schemas.common import DatabaseId, ErrorResponse, Page

router = APIRouter(tags=["catalog"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"model": ErrorResponse, "description": "Not found"},
    status.HTTP_409_CONFLICT: {
        "model": ErrorResponse,
        "description": "Conflicts with existing data",
    },
}


# --- Vehicle models -----------------------------------------------------------


@router.get("/vehicle-models")
def list_vehicle_models(
    service: CatalogServiceDep,
    limit: Limit = 50,
    offset: Offset = 0,
) -> Page[VehicleModelRead]:
    items, total = service.search_models(limit=limit, offset=offset)
    return Page(
        items=[VehicleModelRead.model_validate(m) for m in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/vehicle-models", status_code=status.HTTP_201_CREATED, responses=_ERRORS)
def create_vehicle_model(
    payload: VehicleModelCreate, service: CatalogServiceDep
) -> VehicleModelRead:
    return VehicleModelRead.model_validate(service.create_model(payload))


@router.get("/vehicle-models/{model_id}", responses=_ERRORS)
def get_vehicle_model(model_id: DatabaseId, service: CatalogServiceDep) -> VehicleModelRead:
    return VehicleModelRead.model_validate(service.get_model(model_id))


@router.patch("/vehicle-models/{model_id}", responses=_ERRORS)
def update_vehicle_model(
    model_id: DatabaseId, payload: VehicleModelUpdate, service: CatalogServiceDep
) -> VehicleModelRead:
    return VehicleModelRead.model_validate(service.rename_model(model_id, payload.name))


@router.delete(
    "/vehicle-models/{model_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS
)
def delete_vehicle_model(model_id: DatabaseId, service: CatalogServiceDep) -> Response:
    service.delete_model(model_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Trims --------------------------------------------------------------------


@router.get("/vehicle-models/{model_id}/trims", responses=_ERRORS)
def list_trims(model_id: DatabaseId, service: CatalogServiceDep) -> list[TrimRead]:
    # Not paginated: a model has a handful of trims.
    return [TrimRead.model_validate(t) for t in service.list_trims(model_id)]


@router.post(
    "/vehicle-models/{model_id}/trims", status_code=status.HTTP_201_CREATED, responses=_ERRORS
)
def create_trim(model_id: DatabaseId, payload: TrimCreate, service: CatalogServiceDep) -> TrimRead:
    return TrimRead.model_validate(service.create_trim(model_id, payload))


@router.get("/trims/{trim_id}", responses=_ERRORS)
def get_trim(trim_id: DatabaseId, service: CatalogServiceDep) -> TrimRead:
    return TrimRead.model_validate(service.get_trim(trim_id))


@router.patch("/trims/{trim_id}", responses=_ERRORS)
def update_trim(trim_id: DatabaseId, payload: TrimUpdate, service: CatalogServiceDep) -> TrimRead:
    return TrimRead.model_validate(service.update_trim(trim_id, payload))


@router.delete("/trims/{trim_id}", status_code=status.HTTP_204_NO_CONTENT, responses=_ERRORS)
def delete_trim(trim_id: DatabaseId, service: CatalogServiceDep) -> Response:
    service.delete_trim(trim_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
