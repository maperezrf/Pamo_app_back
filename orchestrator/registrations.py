from orchestrator.core.registry import register_process
from orders.functions.import_falabella_orders import import_falabella_orders
from customers.functions.reconcile_from_shopify import reconcile_from_shopify
from customers.functions.process_customer_webhook import process_customer_webhook
from orchestrator.core.registry import _REGISTRY



print('Registrando')
register_process("orders.import_falabella", import_falabella_orders)
register_process("customers.reconcile_shopify", reconcile_from_shopify)
register_process("customers.process_webhook", process_customer_webhook)


print("*********************** procesos registrados ***********************")
print('\n'.join([f'{i}' for i in  _REGISTRY]))
