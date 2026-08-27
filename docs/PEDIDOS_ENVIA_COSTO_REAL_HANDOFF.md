# Nota para Pedidos: Envía y aprendizaje del costo real

Catálogo Multicanal ya separa dos valores:

- **Precio promedio de envío:** referencia informativa por producto. Se calcula con las guías Envía históricas, ponderadas por la frecuencia real de destinos y por banda de peso facturable. No entra al costo del producto ni reemplaza la tarifa del checkout.
- **Costo real de la guía:** valor definitivo devuelto por Envía después de generar la guía. Este es el dato que debe alimentar el aprendizaje futuro.

## Contrato que debe implementar Pedidos

1. Antes de cotizar, enviar a Envía SKU, cantidad, peso y medidas del paquete, origen y destino completo.
2. La cotización previa es no vinculante. Crear la guía únicamente dentro del flujo autorizado de Pedidos.
3. Al crear o actualizar una guía, persistir localmente un `LogisticsQuoteSnapshot` con `basis=REALIZED_GUIDE`: SKU/variante exacta, costo real, transportadora/servicio, zona de destino sanitizada, peso y medidas usados, fecha y huellas hash de pedido y guía.
4. La escritura debe ser idempotente: una misma guía no puede contarse dos veces.
5. Si el SKU no tiene coincidencia única, conservar el costo como historial no vinculado y enviarlo a revisión; nunca asignarlo a otro producto.
6. El promedio se recalcula automáticamente desde los snapshots `REALIZED_GUIDE`. No sobrescribir peso nativo de Shopify ni sumar este promedio al costo del producto.
7. Mantener separados transporte Envía, alistamiento/bodegaje y costos de Fulfillment.

La referencia actual usa promedio recortado para reducir el efecto de valores extremos y un divisor volumétrico neutral de 5.000 solo para clasificación. La cotización real siempre debe respetar el divisor y las reglas de la transportadora elegida.

## Regla provisional del Catálogo Multicanal

- Un producto sin peso ni medidas completas se clasifica provisionalmente como **menos de 1 kg** y queda marcado como `asumido` y `requiere revisión`. No se presenta como medición confirmada.
- Un lavamanos o lavaplatos real usa el percentil 75 del costo histórico de guías de su propia familia hasta que exista una cotización actual por destino. No se mezcla con el promedio de artículos pequeños.
- Griferías, válvulas, sifones, mangueras, desagües, canastillas y otros accesorios que mencionen lavamanos o lavaplatos no se clasifican como pieza voluminosa.
- Si existe peso nativo de Shopify, se usa para la banda, pero la ausencia de dimensiones continúa visible como pendiente porque Envía factura el mayor entre peso real y volumétrico.
- Cada importación posterior de snapshots sanitizados `REALIZED_GUIDE` actualiza los promedios al reconstruir el catálogo. La captura de nuevas guías debe programarse en Pedidos; hoy no existe un proceso autónomo que consulte Envía en segundo plano.

Caso de control: `010100002-T` es una grifería para lavamanos, registra 1 kg en Shopify y corresponde a la banda **hasta 1 kg**, no a la familia de lavamanos voluminosos.
