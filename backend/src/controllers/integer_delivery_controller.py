from src.services.integer_delivery_service import solve_delivery, list_deliveries


def create_delivery(db, data, user):
    # 成功/失败的审计留痕在 service 的 record_delivery 内统一写入
    return solve_delivery(db, data.run_id, data.relax, user)


def deliveries_for(db, run_id):
    return list_deliveries(db, run_id)
