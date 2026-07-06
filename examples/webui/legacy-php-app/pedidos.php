<?php
//-----------------------------------------------------------------------------
// pedidos.php
// Listado de pedidos + confirmacion de pedido (dispara descuento de stock).
// La carga de renglones de un pedido nuevo esta en pedido_nuevo.php
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

$msg = '';

//-----------------------------------------------------------------------------
// CHUNK IMPORTANTE: confirmar pedido -> descuenta stock de cada producto
//
// Cuando el pedido pasa de estado 'PENDIENTE' a 'CONFIRMADO' hay que
// descontar del inventario la cantidad de cada renglon. Si algun producto
// no tiene stock suficiente NO se confirma el pedido (se corta todo y se
// muestra error), para evitar quedar en negativo silenciosamente.
//-----------------------------------------------------------------------------
if ( isset($_GET['confirmar']) && is_numeric($_GET['confirmar']) )
{
   $idPedido = (int)$_GET['confirmar'];

   $sqlPed = "SELECT estado FROM pedidos WHERE id_pedido = $idPedido";
   $rPed = db_query( $sqlPed );

   if ( $rPed && mysql_num_rows($rPed) == 1 )
   {
      $ped = mysql_fetch_assoc( $rPed );

      if ( $ped['estado'] != 'PENDIENTE' )
      {
         $msg = '<p class="error">El pedido ya fue procesado anteriormente.</p>';
      }
      else
      {
         $sqlRenglones = "SELECT id_producto, cantidad FROM pedido_detalle WHERE id_pedido = $idPedido";
         $rRen = db_query( $sqlRenglones );

         $puedeConfirmar = true;
         $faltantes = array();

         // primera pasada: chequear stock disponible de todos los renglones
         if ( $rRen && mysql_num_rows($rRen) > 0 )
         {
            while ( $ren = mysql_fetch_assoc($rRen) )
            {
               $sqlStock = "SELECT descripcion, stock_actual FROM productos WHERE id_producto = " . (int)$ren['id_producto'];
               $rStock = db_query( $sqlStock );
               if ( $rStock && mysql_num_rows($rStock) == 1 )
               {
                  $prodRow = mysql_fetch_assoc( $rStock );
                  if ( (int)$prodRow['stock_actual'] < (int)$ren['cantidad'] )
                  {
                     $puedeConfirmar = false;
                     $faltantes[] = $prodRow['descripcion'] . ' (disponible: ' . $prodRow['stock_actual'] . ', pedido: ' . $ren['cantidad'] . ')';
                  }
               }
            }
         }

         if ( !$puedeConfirmar )
         {
            $msg = '<p class="error">No se puede confirmar. Stock insuficiente para: ' . implode(', ', $faltantes) . '</p>';
         }
         else
         {
            // segunda pasada: ahora si, descontar stock de cada renglon
            $rRen2 = db_query( $sqlRenglones );
            while ( $ren = mysql_fetch_assoc($rRen2) )
            {
               $sqlUpdate = "UPDATE productos SET stock_actual = stock_actual - " . (int)$ren['cantidad'] .
                            " WHERE id_producto = " . (int)$ren['id_producto'];
               db_query( $sqlUpdate );
            }

            $sqlConfirmar = "UPDATE pedidos SET estado = 'CONFIRMADO', fecha_confirmacion = NOW() WHERE id_pedido = $idPedido";
            db_query( $sqlConfirmar );

            $msg = '<p class="ok">Pedido #' . $idPedido . ' confirmado. Stock actualizado.</p>';
            logAccion( 'CONFIRMAR_PEDIDO', "id_pedido=$idPedido" );
         }
      }
   }
}

//
// LISTADO
//
$sqlLista = "SELECT p.id_pedido, p.fecha, p.estado, c.nombre AS cliente " .
            "FROM pedidos p INNER JOIN clientes c ON c.id_cliente = p.id_cliente " .
            "ORDER BY p.fecha DESC, p.id_pedido DESC LIMIT 50";
$resLista = db_query( $sqlLista );

?>

<h2>Pedidos</h2>

<?php echo $msg; ?>

<p><a href="pedido_nuevo.php">+ Nuevo Pedido</a></p>

<table class="datos" width="100%">
<tr><th>Nro</th><th>Fecha</th><th>Cliente</th><th>Estado</th><th>&nbsp;</th></tr>
<?php
$i = 0;
if ( $resLista && mysql_num_rows($resLista) > 0 )
{
   while ( $p = mysql_fetch_assoc($resLista) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $p['id_pedido'] . "</td>";
      echo "<td>" . formatoFecha($p['fecha']) . "</td>";
      echo "<td>" . $p['cliente'] . "</td>";
      echo "<td>" . $p['estado'] . "</td>";
      echo "<td>";
      if ( $p['estado'] == 'PENDIENTE' )
      {
         echo "<a href=\"pedidos.php?confirmar=" . $p['id_pedido'] . "\" onclick=\"return confirm('Confirmar pedido y descontar stock?')\">confirmar</a> | ";
      }
      echo "<a href=\"facturacion.php?id_pedido=" . $p['id_pedido'] . "\">facturar</a>";
      echo "</td>";
      echo "</tr>";
      $i++;
   }
}
else
{
   echo "<tr><td colspan=\"5\">No hay pedidos cargados</td></tr>";
}
?>
</table>

<?php
require_once( 'inc/footer.php' );
?>
