# Patrón: clientes externos

Los clientes de proveedor viven en `integrations/<provider>/client.py` y
solo encapsulan autenticación, URL, firma, transporte, timeout y errores de
protocolo. La función que expresa una operación de negocio vive en
`integrations/<provider>/functions/`.

## Ejemplo de la separación

```text
función get_customer / create_invoice
        ↓
SiigoClient.request(method, path, ...)
        ↓
API de Siigo
```

No añadir métodos como `create_invoice()` o `get_orders()` a un cliente si
requieren reglas, campos o normalización de una operación concreta.

## Reglas

- Credenciales, endpoints y flags se leen desde `config/constants.py`; no
  usar `os.environ` directo ni valores hardcodeados.
- Definir un timeout explícito.
- Levantar un error propio del proveedor cuando una respuesta HTTP o de
  protocolo sea inválida.
- No exponer secretos en excepciones, logs o respuestas de API.
- Probar la capa de transporte con mocks; las pruebas ordinarias no llaman a
  proveedores reales.
- Revisar funciones, consultas y cliente existentes antes de crear uno.

## Particularidades actuales

Shopify usa GraphQL y distingue errores superiores del documento. Falabella
firma parámetros con HMAC. Envía rechaza redirecciones y limita el tamaño de
respuesta. Siigo reutiliza un token persistido mientras siga vigente. Estas
decisiones se conservan al añadir operaciones del proveedor respectivo.
