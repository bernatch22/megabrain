<?php
//-----------------------------------------------------------------------------
// inventario.php
// ABM basico de productos + ajuste manual de stock (para cuando entra
// mercaderia por compra, no por pedido de venta)
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

if ( $_SESSION['nivel'] < NIVEL_DEPOSITO )
{
   echo '<p class="error">No tiene permisos para acceder a este modulo.</p>';
   require_once( 'inc/footer.php' );
   exit;
}

$msg = '';

//
// ALTA DE PRODUCTO
//
if ( isset($_POST['submit_producto']) && $_POST['submit_producto'] == '1' )
{
   $codigo = limpiar( $_POST['codigo'] );
   $descripcion = limpiar( $_POST['descripcion'] );
   $precioCosto = (float)str_replace(',', '.', $_POST['precio_costo']);
   $precioVenta = (float)str_replace(',', '.', $_POST['precio_venta']);
   $stockInicial = (int)$_POST['stock_inicial'];
   $stockMinimo = isset($_POST['stock_minimo']) && is_numeric($_POST['stock_minimo']) ? (int)$_POST['stock_minimo'] : $GLOBALS['STOCK_MINIMO_DEFAULT'];

   if ( $codigo == '' || $descripcion == '' )
   {
      $msg = '<p class="error">Codigo y descripcion son obligatorios.</p>';
   }
   else
   {
      $sql = "INSERT INTO productos (codigo, descripcion, precio_costo, precio_venta, stock_actual, stock_minimo) VALUES (" .
             "'" . mysql_real_escape_string($codigo) . "', " .
             "'" . mysql_real_escape_string($descripcion) . "', " .
             $precioCosto . ", " .
             $precioVenta . ", " .
             $stockInicial . ", " .
             $stockMinimo . ")";
      $ok = db_query( $sql );
      if ( $ok )
      {
         $msg = '<p class="ok">Producto cargado.</p>';
         logAccion( 'ALTA_PRODUCTO', $codigo );
      }
      else
      {
         $msg = '<p class="error">Error al guardar (verifique que el codigo no este repetido).</p>';
      }
   }
}

//
// AJUSTE MANUAL DE STOCK (entrada de mercaderia por compra)
//
if ( isset($_POST['submit_ajuste']) && $_POST['submit_ajuste'] == '1' )
{
   $idProd = (int)$_POST['id_producto_ajuste'];
   $cantidad = (int)$_POST['cantidad_ajuste'];
   $tipo = $_POST['tipo_ajuste'];  // 'entrada' o 'salida'

   if ( $idProd > 0 && $cantidad > 0 )
   {
      if ( $tipo == 'entrada' )
         $sqlAdj = "UPDATE productos SET stock_actual = stock_actual + $cantidad WHERE id_producto = $idProd";
      else
         $sqlAdj = "UPDATE productos SET stock_actual = stock_actual - $cantidad WHERE id_producto = $idProd";

      db_query( $sqlAdj );
      $msg = '<p class="ok">Stock ajustado.</p>';
      logAccion( 'AJUSTE_STOCK', "id_producto=$idProd tipo=$tipo cantidad=$cantidad" );
   }
}

$resProductos = db_query( "SELECT id_producto, codigo, descripcion, precio_costo, precio_venta, stock_actual, stock_minimo FROM productos ORDER BY descripcion" );

?>

<h2>Inventario</h2>

<?php echo $msg; ?>

<table class="datos" width="100%">
<tr><th>Codigo</th><th>Descripcion</th><th>Costo</th><th>Venta</th><th>Stock</th><th>Minimo</th></tr>
<?php
$i = 0;
if ( $resProductos && mysql_num_rows($resProductos) > 0 )
{
   while ( $p = mysql_fetch_assoc($resProductos) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      if ( (int)$p['stock_actual'] <= (int)$p['stock_minimo'] )
         $clase .= ' error';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $p['codigo'] . "</td>";
      echo "<td>" . $p['descripcion'] . "</td>";
      echo "<td align=\"right\">" . formatoMoneda($p['precio_costo']) . "</td>";
      echo "<td align=\"right\">" . formatoMoneda($p['precio_venta']) . "</td>";
      echo "<td align=\"right\">" . $p['stock_actual'] . "</td>";
      echo "<td align=\"right\">" . $p['stock_minimo'] . "</td>";
      echo "</tr>";
      $i++;
   }
}
?>
</table>

<br>

<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td width="50%" valign="top">

<table class="datos" width="100%">
<tr><th colspan="2">Nuevo Producto</th></tr>
<tr><td colspan="2">
<form method="post" action="inventario.php">
<table cellpadding="2" cellspacing="0" border="0" width="100%">
<tr><td>Codigo:</td><td><input type="text" name="codigo" size="15"></td></tr>
<tr><td>Descripcion:</td><td><input type="text" name="descripcion" size="30"></td></tr>
<tr><td>Precio Costo:</td><td><input type="text" name="precio_costo" size="10"></td></tr>
<tr><td>Precio Venta:</td><td><input type="text" name="precio_venta" size="10"></td></tr>
<tr><td>Stock Inicial:</td><td><input type="text" name="stock_inicial" size="10" value="0"></td></tr>
<tr><td>Stock Minimo:</td><td><input type="text" name="stock_minimo" size="10" value="<?php echo $GLOBALS['STOCK_MINIMO_DEFAULT']; ?>"></td></tr>
<tr><td colspan="2" align="center">
<input type="hidden" name="submit_producto" value="1">
<input type="submit" value="Guardar">
</td></tr>
</table>
</form>
</td></tr>
</table>

</td>
<td width="50%" valign="top">

<table class="datos" width="100%">
<tr><th colspan="2">Ajuste Manual de Stock</th></tr>
<tr><td colspan="2">
<form method="post" action="inventario.php">
<table cellpadding="2" cellspacing="0" border="0" width="100%">
<tr><td>Producto (ID):</td><td><input type="text" name="id_producto_ajuste" size="10"></td></tr>
<tr><td>Cantidad:</td><td><input type="text" name="cantidad_ajuste" size="10"></td></tr>
<tr><td>Tipo:</td><td>
<select name="tipo_ajuste">
<option value="entrada">Entrada (compra)</option>
<option value="salida">Salida (ajuste/merma)</option>
</select>
</td></tr>
<tr><td colspan="2" align="center">
<input type="hidden" name="submit_ajuste" value="1">
<input type="submit" value="Aplicar Ajuste">
</td></tr>
</table>
</form>
</td></tr>
</table>

</td>
</tr>
</table>

<?php
require_once( 'inc/footer.php' );
?>
