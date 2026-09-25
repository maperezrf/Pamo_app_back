from orchestrator.core.registry import register_process
from orders.functions.import_falabella_orders import import_falabella_orders
from orders.functions.import_madecentro_orders import import_madecentro_orders
from orders.functions.process_mercadolibre_notification import process_mercadolibre_notification
from orders.functions.recover_mercadolibre_orders import recover_mercadolibre_orders
from customers.functions.reconcile_from_shopify import reconcile_from_shopify
from customers.functions.process_customer_webhook import process_customer_webhook
from orchestrator.core.registry import _REGISTRY



print('Registrando')
register_process("orders.import_falabella", import_falabella_orders)
register_process("orders.process_mercadolibre_notification", process_mercadolibre_notification)
register_process("orders.recover_mercadolibre", recover_mercadolibre_orders)
register_process("orders.import_madecentro", import_madecentro_orders)
register_process("customers.reconcile_shopify", reconcile_from_shopify)
register_process("customers.process_webhook", process_customer_webhook)


print("*********************** procesos registrados ***********************")
print('\n'.join([f'{i}' for i in  _REGISTRY]))
