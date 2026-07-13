<?php
//-----------------------------------------------------------------------------
// facturacion.php
// Genera la factura a partir de un pedido confirmado. Calcula subtotal,
// descuento por categoria de cliente, IVA y total (ver inc/funciones.php
// calcularTotalFactura() / obtenerDescuentoPorCliente() para el detalle).
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

$msg = '';
$vistaPrevia = null;

if ( !isset($_GET['id_pedido']) || !is_numeric($_GET['id_pedido']) )
{
   echo '<p class="error">Falta indicar el pedido a facturar.</p>';
   require_once( 'inc/footer.php' );
   exit;
}

$idPedido = (int)$_GET['id_pedido'];

$sqlPed = "SELECT p.id_pedido, p.id_cliente, p.estado, c.nombre AS cliente, c.categoria " .
          "FROM pedidos p INNER JOIN clientes c ON c.id_cliente = p.id_cliente " .
          "WHERE p.id_pedido = $idPedido";
$rPed = db_query( $sqlPed );

if ( !$rPed || mysql_num_rows($rPed) != 1 )
{
   echo '<p class="error">Pedido no encontrado.</p>';
   require_once( 'inc/footer.php' );
   exit;
}

$pedido = mysql_fetch_assoc( $rPed );

if ( $pedido['estado'] != 'CONFIRMADO' )
{
   echo '<p class="error">Solo se pueden facturar pedidos en estado CONFIRMADO. Estado actual: ' . $pedido['estado'] . '</p>';
   require_once( 'inc/footer.php' );
   exit;
}

// traer renglones con precio actual del producto
$sqlRen = "SELECT pd.id_producto, pd.cantidad, pr.descripcion, pr.precio_venta " .
          "FROM pedido_detalle pd INNER JOIN productos pr ON pr.id_producto = pd.id_producto " .
          "WHERE pd.id_pedido = $idPedido";
$rRen = db_query( $sqlRen );

$renglones = array();
if ( $rRen && mysql_num_rows($rRen) > 0 )
{
   while ( $ren = mysql_fetch_assoc($rRen) )
   {
      $renglones[] = array(
         'descripcion' => $ren['descripcion'],
         'cantidad' => $ren['cantidad'],
         'precio_unitario' => $ren['precio_venta'],
      );
   }
}

// suma bruta para saber el escalon de descuento por monto de compra
$sumaBruta = 0;
foreach ( $renglones as $r )
{
   $sumaBruta += $r['cantidad'] * $r['precio_unitario'];
}

$descuentoPorc = obtenerDescuentoPorCliente( $pedido['id_cliente'], $sumaBruta );

$totales = calcularTotalFactura( $renglones, $descuentoPorc );

//
// EMITIR (si vino el boton de confirmar emision)
//
if ( isset($_POST['submit_emitir']) && $_POST['submit_emitir'] == '1' )
{
   $sqlFact = "INSERT INTO facturas (id_pedido, id_cliente, fecha, subtotal, descuento_porc, descuento_monto, iva, total, anulada) VALUES (" .
              $idPedido . ", " .
              $pedido['id_cliente'] . ", " .
              "NOW(), " .
              $totales['subtotal'] . ", " .
              $descuentoPorc . ", " .
              $totales['descuento'] . ", " .
              $totales['iva'] . ", " .
              $totales['total'] . ", " .
              "0)";
   db_query( $sqlFact );
   $idFactura = mysql_insert_id();

   db_query( "UPDATE pedidos SET estado = 'FACTURADO' WHERE id_pedido = $idPedido" );

   logAccion( 'EMITIR_FACTURA', "id_factura=$idFactura id_pedido=$idPedido total=" . $totales['total'] );

   $msg = '<p class="ok">Factura #' . $idFactura . ' emitida correctamente por ' . formatoMoneda($totales['total']) . '</p>';
}

?>

<h2>Facturacion - Pedido #<?php echo $idPedido; ?></h2>

<?php echo $msg; ?>

<p>Cliente: <b><?php echo $pedido['cliente']; ?></b> (categoria: <?php echo $pedido['categoria']; ?>)</p>

<table class="datos" width="100%">
<tr><th>Producto</th><th>Cantidad</th><th>Precio Unit.</th><th>Subtotal</th></tr>
<?php
$i = 0;
foreach ( $renglones as $r )
{
   $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
   $sub = $r['cantidad'] * $r['precio_unitario'];
   echo "<tr class=\"$clase\">";
   echo "<td>" . $r['descripcion'] . "</td>";
   echo "<td align=\"right\">" . $r['cantidad'] . "</td>";
   echo "<td align=\"right\">" . formatoMoneda($r['precio_unitario']) . "</td>";
   echo "<td align=\"right\">" . formatoMoneda($sub) . "</td>";
   echo "</tr>";
   $i++;
}
?>
</table>

<br>

<table cellpadding="3" cellspacing="0" border="0" align="right" width="300">
<tr><td>Subtotal:</td><td align="right"><?php echo formatoMoneda($totales['subtotal']); ?></td></tr>
<tr><td>Descuento (<?php echo $descuentoPorc; ?>%):</td><td align="right">-<?php echo formatoMoneda($totales['descuento']); ?></td></tr>
<tr><td>Subtotal con descuento:</td><td align="right"><?php echo formatoMoneda($totales['subtotal_con_descuento']); ?></td></tr>
<tr><td>IVA (<?php echo $GLOBALS['IVA_PORCENTAJE']; ?>%):</td><td align="right"><?php echo formatoMoneda($totales['iva']); ?></td></tr>
<tr><td><b>TOTAL:</b></td><td align="right"><b><?php echo formatoMoneda($totales['total']); ?></b></td></tr>
</table>

<br clear="all">

<?php if ( $pedido['estado'] == 'CONFIRMADO' ) { ?>
<form method="post" action="facturacion.php?id_pedido=<?php echo $idPedido; ?>">
<input type="hidden" name="submit_emitir" value="1">
<input type="submit" value="Emitir Factura" onclick="return confirm('Emitir factura definitiva?')">
</form>
<?php } ?>

<?php
require_once( 'inc/footer.php' );
?>
