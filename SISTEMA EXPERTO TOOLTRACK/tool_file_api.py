"""Tool file: everything the system knows about one physical tool, on a single screen.

Identity, where it is now, how it has been used (sharpening and grinding), what happened to it
(incidents, Maintenance tickets) and every movement. The service, incident and ticket sections are
only sent to roles that can see them.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import func

import auth
import records_api
import scan_codes
import tool_images
import tool_params
import tool_tracking_api
import workflow_api
from models.inventory import (
    BajaHerramienta,
    HerramientaCatalogo,
    IncidenteHerramienta,
    MovimientoHerramienta,
    NumeroHerramienta,
    ServicioHerramienta,
    SolicitudServicio,
    TicketMantenimiento,
)

router = APIRouter(prefix="/workflow", tags=["tool file"])

SECTIONS = (
    ("movements", "all"),
    ("requests", None),
    ("incidents", None),
    ("tickets", None),
    ("retirements", None),
    ("services", None),
)
SERVICE_SECTIONS = {"requests", "incidents", "tickets", "retirements", "services"}


def _iso(value):
    return value.isoformat() if value else None


@router.get("/tools/{tool_id}/file")
def get_tool_file(tool_id: str):
    user = auth.current_user()
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    can_see_service = bool({"service.view", "maintenance.view"} & set(user.permissions))
    db = workflow_api.SessionLocal()
    try:
        tool = scan_codes.find_tool(db, tool_id)
        if not tool:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_id}' is not in the inventory")
        described = tool_tracking_api._describe_tool(db, tool, *tool_tracking_api._location_context(db))
        linked = db.query(HerramientaCatalogo.variante_id).filter(HerramientaCatalogo.herramienta_id == tool.id).first()
        params = tool_params.load(db)
        stripper = tool_params.stripper_for(params, tool.size, tool.stripper_override) if tool.categoria == "punzon" else None
        tn_row = db.query(NumeroHerramienta).filter(NumeroHerramienta.tn == tool.tn).first() if tool.tn else None
        identity = {
            "id": tool.id,
            "tn": tool.tn,
            "size": tool.size,
            "type": tool.type,
            "life": tool.life,
            "altura_total": tool.altura_total,
            "stripper": f"{stripper:.3f}" if stripper is not None else None,
            "stripper_custom": bool(tool.stripper_override),
            "life_alert": tool_params.life_alert(tool.categoria, tool.life, params),
            "life_minimum": f"{params['punch_min_life' if tool.categoria == 'punzon' else 'die_min_height']:.3f}"
            if tool.categoria in ("punzon", "matriz") else None,
            "clearance": tool.clearance,
            "so": tool.so,
            "proveedor": tool.proveedor,
            "image": tool_images.image_source(tool, tn_row),
            "categoria": tool.categoria,
            "ubicacion": tool.ubicacion,
            "catalog_linked": linked is not None and linked[0] is not None,
            "estado": tool.estado or "disponible",
        }

        names = records_api._names(db)
        sections = {}
        for key, group in SECTIONS:
            if key in SERVICE_SECTIONS and not can_see_service:
                continue
            kind = records_api.KINDS[key]
            query = kind.query(db, records_api.Filters(tool=tool.id, exact_tool=True, group=group))
            sections[key] = {
                "label": kind.label,
                "columns": [{"key": c.key, "label": c.label, "export_only": c.export_only} for c in kind.columns],
                "total": query.count(),
                "rows": [kind.row(item, names) for item in query.limit(records_api.MAX_PAGE).all()],
                "groups": [{"value": "all", "label": "All"}] + [{"value": k, "label": v} for k, v in kind.groups.items()],
            }

        movements = db.query(
            func.count(MovimientoHerramienta.id), func.min(MovimientoHerramienta.creado_en), func.max(MovimientoHerramienta.creado_en),
        ).filter(MovimientoHerramienta.herramienta_id == tool.id).one()
        summary = {"movements": movements[0], "first_movement_at": _iso(movements[1]), "last_movement_at": _iso(movements[2])}
        if can_see_service:
            services = db.query(ServicioHerramienta).filter(ServicioHerramienta.herramienta_id == tool.id).all()
            open_ticket = db.query(TicketMantenimiento).filter(
                TicketMantenimiento.herramienta_id == tool.id, TicketMantenimiento.estado != "closed",
            ).first()
            pending = db.query(BajaHerramienta).filter(
                BajaHerramienta.herramienta_id == tool.id, BajaHerramienta.estado == "pending",
            ).first()
            open_request = db.query(SolicitudServicio).filter(
                SolicitudServicio.herramienta_id == tool.id, SolicitudServicio.estado.in_(("open", "attending", "collected", "replaced")),
            ).first()
            summary.update({
                "sharpenings": sum(1 for item in services if item.tipo == "sharpening"),
                "ground_off": round(sum(item.rebaje or 0 for item in services), 3),
                "abnormal": sum(1 for item in services if item.anormal),
                "incidents": db.query(IncidenteHerramienta).filter(IncidenteHerramienta.herramienta_id == tool.id).count(),
                "tickets": db.query(TicketMantenimiento).filter(TicketMantenimiento.herramienta_id == tool.id).count(),
                "open_ticket": f"MT-{open_ticket.id:05d}" if open_ticket else None,
                "retirements": db.query(BajaHerramienta).filter(BajaHerramienta.herramienta_id == tool.id).count(),
                "pending_retirement": f"BJ-{pending.id:05d}" if pending else None,
                "open_request": f"SR-{open_request.id:05d}" if open_request else None,
                "requests": db.query(SolicitudServicio).filter(
                    (SolicitudServicio.herramienta_id == tool.id) | (SolicitudServicio.herramienta_nueva_id == tool.id)
                ).count(),
            })
        return {"tool": identity, "where": described, "summary": summary, "sections": sections}
    finally:
        db.close()
