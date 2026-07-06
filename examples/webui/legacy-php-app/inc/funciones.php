<?php
//-----------------------------------------------------------------------------
// funciones.php
// Funciones varias reusadas en todo el sistema. Bolsa de gatos.
//-----------------------------------------------------------------------------

function limpiar( $s )
{
   $s = trim( $s );
   $s = stripslashes( $s );
   $s = htmlspecialchars( $s );
   return $s;
}

function formatoMoneda( $n )
{
   return '$ ' . number_format( (float)$n, 2, ',', '.' );
}

function formatoFecha( $f )
{
   // espera formato mysql YYYY-MM-DD
   if ( empty( $f ) || $f === '0000-00-00' )
      return '-';
   $p = explode( '-', $f );
   if ( count($p) != 3 )
      return $f;
   return $p[2] . '/' . $p[1] . '/' . $p[0];
}

//-----------------------------------------------------------------------------
// calcularTotalFactura()
//
// CHUNK IMPORTANTE: calculo del total de una factura.
// Reglas del negocio (no cambiar sin autorizacion de administracion):
//   1) subtotal = suma (cantidad * precio_unitario) de cada renglon
//   2) se aplica el descuento (%) ANTES de calcular el IVA
//   3) el IVA se calcula sobre el subtotal ya descontado
//   4) total = subtotal_con_descuento + iva
//
// $renglones = array de arrays con 'cantidad' y 'precio_unitario'
// $descuentoPorc = descuento en porcentaje (ej: 10 = 10%)
//-----------------------------------------------------------------------------
function calcularTotalFactura( $renglones, $descuentoPorc = 0 )
{
   global $IVA_PORCENTAJE;

   $subtotal = 0;
   for ( $i = 0; $i < count($renglones); $i++ )
   {
      $cant = (float)$renglones[$i]['cantidad'];
      $precio = (float)$renglones[$i]['precio_unitario'];
      $subtotal += $cant * $precio;
   }

   $montoDescuento = $subtotal * ( (float)$descuentoPorc / 100 );
   $subtotalConDescuento = $subtotal - $montoDescuento;

   $iva = $subtotalConDescuento * ( (float)$GLOBALS['IVA_PORCENTAJE'] / 100 );

   $total = $subtotalConDescuento + $iva;

   $ret = array();
   $ret['subtotal'] = $subtotal;
   $ret['descuento'] = $montoDescuento;
   $ret['subtotal_con_descuento'] = $subtotalConDescuento;
   $ret['iva'] = $iva;
   $ret['total'] = $total;

   return $ret;
}

//-----------------------------------------------------------------------------
// obtenerDescuentoPorCliente()
//
// CHUNK: regla de descuento segun categoria del cliente y monto de la compra.
// Historico: se agrego el escalon de "mayorista" en 2004 a pedido de ventas.
//-----------------------------------------------------------------------------
function obtenerDescuentoPorCliente( $idCliente, $montoCompra )
{
   $sql = "SELECT categoria FROM clientes WHERE id_cliente = " . (int)$idCliente;
   $res = db_query( $sql );
   $cat = 'MINORISTA';
   if ( $res && mysql_num_rows($res) > 0 )
   {
      $row = mysql_fetch_assoc( $res );
      $cat = $row['categoria'];
   }

   $descuento = 0;

   if ( $cat == 'MAYORISTA' )
   {
      $descuento = 15;
      if ( $montoCompra > 50000 )
         $descuento = 20;
   }
   else if ( $cat == 'REVENDEDOR' )
   {
      $descuento = 10;
   }
   else  // MINORISTA
   {
      if ( $montoCompra > 20000 )
         $descuento = 5;
      else
         $descuento = 0;
   }

   return $descuento;
}

function redireccionar( $url )
{
   header( "Location: $url" );
   exit;
}

function logAccion( $accion, $detalle = '' )
{
   $usuario = isset($_SESSION['usuario']) ? $_SESSION['usuario'] : 'desconocido';
   $sql = "INSERT INTO log_acciones (usuario, accion, detalle, fecha) VALUES ('" .
          mysql_real_escape_string($usuario) . "', '" .
          mysql_real_escape_string($accion) . "', '" .
          mysql_real_escape_string($detalle) . "', NOW())";
   db_query( $sql );
}

?>
