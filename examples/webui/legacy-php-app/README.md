# GestPyme — Sistema de Gestión Comercial (circa 2003)

Aplicación PHP de muestra, **inventada** para servir como input de demo a una
herramienta de code-retrieval (megabrain). Imita el estilo de un sistema PHP
procedural de principios de los 2000: HTML y PHP mezclados, SQL inline,
llamadas estilo `mysql_*`, variables globales, tablas para maquetar el layout,
estilos inline, y muy pocos comentarios salvo en los puntos de lógica de
negocio real. No contiene datos, credenciales ni contenido real de ningún
cliente — es 100% ficticio, solo imita el ESTILO de un código legacy real.

## Árbol de archivos

```
legacy-php-app/
├── README.md                  este archivo
├── login.php                  pantalla de login + validación de credenciales/nivel
├── logout.php                 destruye la sesión y redirige a login
├── main.php                   panel principal: pedidos de hoy, facturado del mes, stock bajo
├── clientes.php                ABM de clientes (alta + búsqueda + listado)
├── pedidos.php                 listado de pedidos + confirmación (dispara descuento de stock)
├── pedido_nuevo.php            carga de un pedido nuevo con hasta 8 renglones
├── facturacion.php             genera factura desde un pedido confirmado (subtotal/desc/IVA/total)
├── inventario.php               ABM de productos + ajuste manual de stock
├── reportes.php                 reporte de ventas por cliente (agregación por rango de fechas)
├── ajax_stock.php               endpoint texto-plano que devuelve stock de un producto (semi-muerto)
├── instalar.php                  script de instalación: crea tablas + usuario admin por defecto
├── inc/
│   ├── config.php               config general, constantes de nivel, % IVA, arranca sesión
│   ├── db.php                    conexión mysql_connect + helper db_query()
│   ├── funciones.php             funciones compartidas: formato, cálculo de factura, descuentos
│   ├── header.php                header HTML + menú + chequeo de sesión activa
│   └── footer.php                cierre de HTML + mysql_close()
└── admin/
    └── usuarios.php              ABM de usuarios del sistema (solo nivel administrador)
```

**17 archivos .php, ~1555 líneas totales.**

## Preguntas de ejemplo para probar retrieval

| # | Pregunta | Archivo objetivo | Función / ubicación aproximada |
|---|----------|-------------------|--------------------------------|
| 1 | ¿Cómo se calcula el total de la factura con IVA y descuento? | `inc/funciones.php` | función `calcularTotalFactura()` — línea 44 (subtotal → descuento → IVA sobre el subtotal descontado → total) |
| 2 | ¿Dónde se valida el login y se arma el nivel de permisos del usuario? | `login.php` | bloque "CHUNK IMPORTANTE: validación de login y permisos" — línea 16 (compara `md5(clave)`, chequea `activo`, setea `$_SESSION['nivel']`) |
| 3 | ¿Dónde se descuenta stock al confirmar un pedido? | `pedidos.php` | bloque "CHUNK IMPORTANTE: confirmar pedido -> descuenta stock" — línea 13 (chequea stock disponible de todos los renglones antes de descontar, corta si falta stock) |
| 4 | ¿Cómo se calcula el descuento por categoría de cliente? | `inc/funciones.php` | función `obtenerDescuentoPorCliente()` — línea 79 (escalones MAYORISTA/REVENDEDOR/MINORISTA según categoría y monto de compra) |
| 5 | ¿Dónde se genera el reporte de ventas por cliente / ranking de facturación? | `reportes.php` | bloque "CHUNK IMPORTANTE: reporte de ventas por cliente (agregación)" — línea 21 (`SUM`/`COUNT`/`AVG` de facturas agrupado por cliente, excluye anuladas) |

### Extras (no listados arriba pero también recuperables)
- Chequeo de permisos por nivel de acceso repetido en `admin/usuarios.php` (línea 15, `NIVEL_ADMIN`) e `inventario.php`/`reportes.php` (`NIVEL_DEPOSITO`/`NIVEL_VENDEDOR`).
- Emisión de factura (INSERT + cambio de estado del pedido a `FACTURADO`) en `facturacion.php`.
- Alta de pedido con renglones dinámicos (hardcodeado a 8 ítems) en `pedido_nuevo.php`.
