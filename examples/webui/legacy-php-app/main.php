<?php
//-----------------------------------------------------------------------------
// main.php
// Pantalla de inicio con resumen rapido (pedidos del dia, stock bajo, etc)
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

$hoy = date('Y-m-d');

// pedidos de hoy
$sql1 = "SELECT COUNT(*) AS cant FROM pedidos WHERE fecha = '$hoy'";
$r1 = db_query( $sql1 );
$cantPedidosHoy = 0;
if ( $r1 && mysql_num_rows($r1) > 0 )
{
   $row1 = mysql_fetch_assoc($r1);
   $cantPedidosHoy = $row1['cant'];
}

// facturado del mes
$mesActual = date('Y-m');
$sql2 = "SELECT SUM(total) AS suma FROM facturas WHERE DATE_FORMAT(fecha,'%Y-%m') = '$mesActual' AND anulada = 0";
$r2 = db_query( $sql2 );
$facturadoMes = 0;
if ( $r2 && mysql_num_rows($r2) > 0 )
{
   $row2 = mysql_fetch_assoc($r2);
   $facturadoMes = $row2['suma'];
   if ( $facturadoMes == '' )
      $facturadoMes = 0;
}

// productos con stock bajo (usa columna stock_minimo si existe, sino el default global)
$sql3 = "SELECT codigo, descripcion, stock_actual, stock_minimo FROM productos WHERE stock_actual <= stock_minimo ORDER BY stock_actual ASC LIMIT 10";
$r3 = db_query( $sql3 );

?>

<h2>Panel Principal</h2>

<table width="100%" cellpadding="8" cellspacing="0" border="0">
<tr>
<td width="33%" valign="top">
<table class="datos" width="100%">
<tr><th colspan="2">Resumen de Hoy</th></tr>
<tr><td>Pedidos ingresados hoy</td><td align="right"><b><?php echo $cantPedidosHoy; ?></b></td></tr>
<tr><td>Facturado este mes</td><td align="right"><b><?php echo formatoMoneda($facturadoMes); ?></b></td></tr>
</table>
</td>
<td width="67%" valign="top">
<table class="datos" width="100%">
<tr><th colspan="4">Productos con Stock Bajo</th></tr>
<tr><th>Codigo</th><th>Descripcion</th><th>Stock Actual</th><th>Stock Minimo</th></tr>
<?php
$i = 0;
if ( $r3 && mysql_num_rows($r3) > 0 )
{
   while ( $prod = mysql_fetch_assoc($r3) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $prod['codigo'] . "</td>";
      echo "<td>" . $prod['descripcion'] . "</td>";
      echo "<td align=\"right\">" . $prod['stock_actual'] . "</td>";
      echo "<td align=\"right\">" . $prod['stock_minimo'] . "</td>";
      echo "</tr>";
      $i++;
   }
}
else
{
   echo "<tr><td colspan=\"4\">No hay productos con stock bajo</td></tr>";
}
?>
</table>
</td>
</tr>
</table>

<?php
require_once( 'inc/footer.php' );
?>
