from pydantic import BaseModel
from typing import Optional


class EquipmentCreate(BaseModel):
    name: str
    category: str
    serial_num: Optional[str] = None
    model: Optional[str] = None
    manufacturer: Optional[str] = None
    location: Optional[str] = None
    assigned_to: Optional[str] = None
    status: str = "active"
    notes: Optional[str] = None
    purchase_date: Optional[str] = None
    warranty_expiry: Optional[str] = None
    end_of_life_date: Optional[str] = None


class EquipmentUpdate(EquipmentCreate):
    pass


class MaintenanceTaskCreate(BaseModel):
    equipment_id: int
    title: str
    description: Optional[str] = None
    task_type: str = "scheduled"
    interval_days: Optional[int] = None
    last_done: Optional[str] = None
    next_due: Optional[str] = None
    status: str = "pending"
    assigned_to: Optional[str] = None
    notes: Optional[str] = None


class PartUsed(BaseModel):
    item_id: int
    quantity_used: float = 1.0
    notes: Optional[str] = None


class MaintenanceComplete(BaseModel):
    completed_by: Optional[str] = None
    notes: Optional[str] = None
    next_due: Optional[str] = None
    parts_used: Optional[list[PartUsed]] = None


class CalibrationCreate(BaseModel):
    equipment_id: int
    calibrated_by: Optional[str] = None
    calibrated_at: str
    next_due: Optional[str] = None
    certificate_num: Optional[str] = None
    result: str = "pass"
    notes: Optional[str] = None


class InventoryItemCreate(BaseModel):
    name: str
    part_number: Optional[str] = None
    category: Optional[str] = None
    location: Optional[str] = None
    quantity: int = 0
    unit: str = "ea"
    min_stock: int = 0
    unit_cost: Optional[float] = None
    supplier: Optional[str] = None
    notes: Optional[str] = None


class InventoryAdjust(BaseModel):
    action: str  # "add" | "remove" | "set"
    quantity: int
    reference: Optional[str] = None
    performed_by: Optional[str] = None


class CalibrationBulkEdit(BaseModel):
    ids: list[int]
    calibrated_at: Optional[str] = None
    next_due: Optional[str] = None
    calibrated_by: Optional[str] = None
    result: Optional[str] = None
    notes: Optional[str] = None


class MaintenanceBulkCreate(BaseModel):
    title: str
    description: Optional[str] = None
    task_type: str = "scheduled"
    interval_days: Optional[int] = None
    next_due: Optional[str] = None
    status: str = "pending"
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    category: Optional[str] = None       # filter by equipment category
    name_contains: Optional[str] = None  # filter by equipment name substring


class SkoCreate(BaseModel):
    name: str
    nsn: Optional[str] = None
    description: Optional[str] = None
    notes: Optional[str] = None
    equipment_ids: Optional[list[int]] = None


class SkoCheckout(BaseModel):
    checked_out_by: str
    expected_return: Optional[str] = None
    notes: Optional[str] = None


class SkoCheckin(BaseModel):
    notes: Optional[str] = None


class SkoPartsUsed(BaseModel):
    item_id: int
    quantity: float = 1.0
    used_by: Optional[str] = None
    notes: Optional[str] = None
