<?php
//-----------------------------------------------------------------------------
// clientes.php
// ABM de clientes (alta / listado / busqueda). La edicion individual esta en
// cliente_editar.php porque en su momento se penso separar "modulos" pero
// nunca se termino de limpiar.
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );
require_once( 'inc/header.php' );

$msg = '';

//
// ALTA DE CLIENTE NUEVO
//
if ( isset($_POST['submit_nuevo']) && $_POST['submit_nuevo'] == '1' )
{
   $nombre = limpiar( $_POST['nombre'] );
   $cuit = limpiar( $_POST['cuit'] );
   $direccion = limpiar( $_POST['direccion'] );
   $telefono = limpiar( $_POST['telefono'] );
   $categoria = limpiar( $_POST['categoria'] );

   if ( $nombre == '' )
   {
      $msg = '<p class="error">Falta el nombre del cliente.</p>';
   }
   else
   {
      $sql = "INSERT INTO clientes (nombre, cuit, direccion, telefono, categoria, fecha_alta) VALUES (" .
             "'" . mysql_real_escape_string($nombre) . "', " .
             "'" . mysql_real_escape_string($cuit) . "', " .
             "'" . mysql_real_escape_string($direccion) . "', " .
             "'" . mysql_real_escape_string($telefono) . "', " .
             "'" . mysql_real_escape_string($categoria) . "', " .
             "NOW())";
      $ok = db_query( $sql );
      if ( $ok )
      {
         $msg = '<p class="ok">Cliente dado de alta correctamente.</p>';
         logAccion( 'ALTA_CLIENTE', $nombre );
      }
      else
      {
         $msg = '<p class="error">Error al guardar el cliente.</p>';
      }
   }
}

//
// BUSQUEDA
//
$busca = isset($_GET['busca']) ? limpiar($_GET['busca']) : '';

$sqlLista = "SELECT id_cliente, nombre, cuit, telefono, categoria FROM clientes WHERE 1=1 ";
if ( $busca != '' )
{
   $buscaEsc = mysql_real_escape_string( $busca );
   $sqlLista .= " AND (nombre LIKE '%$buscaEsc%' OR cuit LIKE '%$buscaEsc%') ";
}
$sqlLista .= " ORDER BY nombre ASC";

$resLista = db_query( $sqlLista );

?>

<h2>Clientes</h2>

<?php echo $msg; ?>

<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td width="60%" valign="top">

<form method="get" action="clientes.php">
Buscar: <input type="text" name="busca" value="<?php echo htmlspecialchars($busca); ?>" size="25">
<input type="submit" value="Buscar">
</form>

<table class="datos" width="100%">
<tr><th>Nombre</th><th>CUIT</th><th>Telefono</th><th>Categoria</th><th>&nbsp;</th></tr>
<?php
$i = 0;
if ( $resLista && mysql_num_rows($resLista) > 0 )
{
   while ( $c = mysql_fetch_assoc($resLista) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $c['nombre'] . "</td>";
      echo "<td>" . $c['cuit'] . "</td>";
      echo "<td>" . $c['telefono'] . "</td>";
      echo "<td>" . $c['categoria'] . "</td>";
      echo "<td><a href=\"cliente_editar.php?id=" . $c['id_cliente'] . "\">editar</a></td>";
      echo "</tr>";
      $i++;
   }
}
else
{
   echo "<tr><td colspan=\"5\">Sin resultados</td></tr>";
}
?>
</table>

</td>
<td width="40%" valign="top">

<table class="datos" width="100%">
<tr><th colspan="2">Nuevo Cliente</th></tr>
<tr><td colspan="2">
<form method="post" action="clientes.php">
<table cellpadding="2" cellspacing="0" border="0" width="100%">
<tr><td>Nombre:</td><td><input type="text" name="nombre" size="25"></td></tr>
<tr><td>CUIT:</td><td><input type="text" name="cuit" size="15"></td></tr>
<tr><td>Direccion:</td><td><input type="text" name="direccion" size="25"></td></tr>
<tr><td>Telefono:</td><td><input type="text" name="telefono" size="15"></td></tr>
<tr><td>Categoria:</td><td>
<select name="categoria">
<option value="MINORISTA">Minorista</option>
<option value="REVENDEDOR">Revendedor</option>
<option value="MAYORISTA">Mayorista</option>
</select>
</td></tr>
<tr><td colspan="2" align="center">
<input type="hidden" name="submit_nuevo" value="1">
<input type="submit" value="Guardar">
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
