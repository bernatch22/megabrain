<?php
//-----------------------------------------------------------------------------
// pedido_nuevo.php
// Carga de un pedido nuevo con renglones dinamicos (hasta 8 items, no se
// hizo generico con JS porque en 2003 no daba el tiempo, quedo hardcodeado)
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

$MAX_RENGLONES = 8;

$msg = '';

if ( isset($_POST['submit_pedido']) && $_POST['submit_pedido'] == '1' )
{
   $idCliente = (int)$_POST['id_cliente'];

   if ( $idCliente <= 0 )
   {
      $msg = '<p class="error">Debe seleccionar un cliente.</p>';
   }
   else
   {
      $sqlPed = "INSERT INTO pedidos (id_cliente, fecha, estado) VALUES ($idCliente, CURDATE(), 'PENDIENTE')";
      db_query( $sqlPed );
      $idPedido = mysql_insert_id();

      $renglonesInsertados = 0;
      for ( $i = 1; $i <= $MAX_RENGLONES; $i++ )
      {
         $campo = 'producto_' . $i;
         $campoCant = 'cantidad_' . $i;

         if ( isset($_POST[$campo]) && is_numeric($_POST[$campo]) && (int)$_POST[$campo] > 0 )
         {
            $idProd = (int)$_POST[$campo];
            $cant = isset($_POST[$campoCant]) ? (int)$_POST[$campoCant] : 0;

            if ( $cant > 0 )
            {
               $sqlDet = "INSERT INTO pedido_detalle (id_pedido, id_producto, cantidad) VALUES ($idPedido, $idProd, $cant)";
               db_query( $sqlDet );
               $renglonesInsertados++;
            }
         }
      }

      if ( $renglonesInsertados == 0 )
      {
         // pedido vacio, lo borramos para no dejar basura
         db_query( "DELETE FROM pedidos WHERE id_pedido = $idPedido" );
         $msg = '<p class="error">El pedido no tiene renglones cargados, no se guardo.</p>';
      }
      else
      {
         logAccion( 'ALTA_PEDIDO', "id_pedido=$idPedido renglones=$renglonesInsertados" );
         redireccionar( '/pedidos.php' );
      }
   }
}

$resClientes = db_query( "SELECT id_cliente, nombre FROM clientes ORDER BY nombre" );
$resProductos = db_query( "SELECT id_producto, codigo, descripcion, precio_venta FROM productos ORDER BY descripcion" );

// hay que armar el combo de productos dos veces (una por renglon), guardamos el html en una var
$comboProductos = '<option value="">-- producto --</option>';
if ( $resProductos && mysql_num_rows($resProductos) > 0 )
{
   while ( $pr = mysql_fetch_assoc($resProductos) )
   {
      $comboProductos .= '<option value="' . $pr['id_producto'] . '">' . $pr['codigo'] . ' - ' . $pr['descripcion'] . ' (' . formatoMoneda($pr['precio_venta']) . ')</option>';
   }
   mysql_data_seek( $resProductos, 0 );
}

?>

<h2>Nuevo Pedido</h2>

<?php echo $msg; ?>

<form method="post" action="pedido_nuevo.php">

<table class="datos" width="100%">
<tr><th colspan="2">Datos del Pedido</th></tr>
<tr><td>Cliente:</td><td>
<select name="id_cliente">
<option value="">-- seleccione --</option>
<?php
if ( $resClientes && mysql_num_rows($resClientes) > 0 )
{
   while ( $cl = mysql_fetch_assoc($resClientes) )
   {
      echo '<option value="' . $cl['id_cliente'] . '">' . $cl['nombre'] . '</option>';
   }
}
?>
</select>
</td></tr>
</table>

<br>

<table class="datos" width="100%">
<tr><th>Producto</th><th>Cantidad</th></tr>
<?php
for ( $i = 1; $i <= $MAX_RENGLONES; $i++ )
{
   echo '<tr>';
   echo '<td><select name="producto_' . $i . '">' . $comboProductos . '</select></td>';
   echo '<td><input type="text" name="cantidad_' . $i . '" size="5" value="0"></td>';
   echo '</tr>';
}
?>
</table>

<br>
<input type="hidden" name="submit_pedido" value="1">
<input type="submit" value="Guardar Pedido">

</form>

<?php
require_once( 'inc/footer.php' );
?>
