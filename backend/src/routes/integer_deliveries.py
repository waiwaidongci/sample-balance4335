from fastapi import APIRouter, Depends
from fastapi.responses import Response

from src.config.database import session
from src.middlewares.auth import current_user, require
from src.schemas.integerization import IntegerizeInput
from src.services.export_service import export_csv, export_integer_csv
from src.services.integer_delivery_service import list_deliveries
from src.controllers.integer_delivery_controller import create_delivery

router = APIRouter(prefix='/runs', tags=['integer-delivery'])


@router.post('/integerize', status_code=201)
def integerize_run(data: IntegerizeInput, db=Depends(session),
                   user=Depends(require('admin', 'analyst'))):
    return create_delivery(db, data, user)


@router.get('/{run_id}/integer-deliveries')
def deliveries(run_id: int, db=Depends(session), user=Depends(current_user)):
    return list_deliveries(db, run_id)


@router.get('/{run_id}/export')
def export(run_id: int, db=Depends(session), user=Depends(current_user)):
    return Response(
        export_csv(db, run_id), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition': f'attachment; filename="weights-{run_id}.csv"'})


@router.get('/{run_id}/export-integer')
def export_integer(run_id: int, relax: int = 0, db=Depends(session),
                   user=Depends(current_user)):
    return Response(
        export_integer_csv(db, run_id, relax), media_type='text/csv; charset=utf-8',
        headers={'Content-Disposition':
                 f'attachment; filename="weights-integer-{run_id}-k{relax}.csv"'})
