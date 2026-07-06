<?php
//-----------------------------------------------------------------------------
// reportes.php
// Reporte de ventas por cliente en un rango de fechas, con totales
// acumulados. Usado por administracion para cierre mensual.
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

if ( $_SESSION['nivel'] < NIVEL_VENDEDOR )
{
   echo '<p class="error">No tiene permisos para ver reportes.</p>';
   require_once( 'inc/footer.php' );
   exit;
}

$desde = isset($_GET['desde']) ? limpiar($_GET['desde']) : date('Y-m-01');
$hasta = isset($_GET['hasta']) ? limpiar($_GET['hasta']) : date('Y-m-d');

//-----------------------------------------------------------------------------
// CHUNK IMPORTANTE: reporte de ventas por cliente (agregacion)
//
// Suma el total facturado por cliente dentro del rango de fechas,
// excluyendo facturas anuladas, y trae tambien la cantidad de facturas
// y el ticket promedio. Ordenado de mayor a menor facturacion, sirve
// para armar el ranking de clientes que pide administracion todos los
// meses.
//-----------------------------------------------------------------------------
$desdeEsc = mysql_real_escape_string( $desde );
$hastaEsc = mysql_real_escape_string( $hasta );

$sqlReporte = "SELECT c.id_cliente, c.nombre, c.categoria, " .
              "COUNT(f.id_factura) AS cant_facturas, " .
              "SUM(f.total) AS total_facturado, " .
              "AVG(f.total) AS ticket_promedio " .
              "FROM facturas f " .
              "INNER JOIN clientes c ON c.id_cliente = f.id_cliente " .
              "WHERE f.anulada = 0 " .
              "AND f.fecha >= '$desdeEsc' " .
              "AND f.fecha <= '$hastaEsc' " .
              "GROUP BY c.id_cliente, c.nombre, c.categoria " .
              "ORDER BY total_facturado DESC";

$resReporte = db_query( $sqlReporte );

$totalGeneral = 0;

?>

<h2>Reporte de Ventas por Cliente</h2>

<form method="get" action="reportes.php">
Desde: <input type="text" name="desde" value="<?php echo htmlspecialchars($desde); ?>" size="10"> (YYYY-MM-DD)
Hasta: <input type="text" name="hasta" value="<?php echo htmlspecialchars($hasta); ?>" size="10">
<input type="submit" value="Generar">
</form>

<br>

<table class="datos" width="100%">
<tr><th>Cliente</th><th>Categoria</th><th>Cant. Facturas</th><th>Total Facturado</th><th>Ticket Promedio</th></tr>
<?php
$i = 0;
if ( $resReporte && mysql_num_rows($resReporte) > 0 )
{
   while ( $r = mysql_fetch_assoc($resReporte) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $r['nombre'] . "</td>";
      echo "<td>" . $r['categoria'] . "</td>";
      echo "<td align=\"right\">" . $r['cant_facturas'] . "</td>";
      echo "<td align=\"right\">" . formatoMoneda($r['total_facturado']) . "</td>";
      echo "<td align=\"right\">" . formatoMoneda($r['ticket_promedio']) . "</td>";
      echo "</tr>";
      $totalGeneral += $r['total_facturado'];
      $i++;
   }
}
else
{
   echo "<tr><td colspan=\"5\">Sin datos para el periodo seleccionado</td></tr>";
}
?>
<tr><td colspan="3" align="right"><b>Total General:</b></td><td align="right"><b><?php echo formatoMoneda($totalGeneral); ?></b></td><td>&nbsp;</td></tr>
</table>

<?php
require_once( 'inc/footer.php' );
?>
