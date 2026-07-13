<?php
//-----------------------------------------------------------------------------
// admin/usuarios.php
// ABM de usuarios del sistema, solo para nivel administrador.
// OJO: este archivo esta en /admin pero usa los includes de la raiz con ../
//-----------------------------------------------------------------------------
require_once( '../inc/config.php' );
require_once( '../inc/header.php' );

//
// chequeo de nivel repetido aca (deberia estar centralizado pero cada
// pagina admin lo vuelve a chequear por las dudas, viejo bug de 2003
// donde alguien accedio directo a la url sin pasar por el menu)
//
if ( !isset($_SESSION['nivel']) || $_SESSION['nivel'] < NIVEL_ADMIN )
{
   echo '<p class="error">Acceso denegado. Se requiere nivel administrador.</p>';
   require_once( '../inc/footer.php' );
   exit;
}

$msg = '';

if ( isset($_POST['submit_usuario']) && $_POST['submit_usuario'] == '1' )
{
   $nombre = limpiar( $_POST['nombre'] );
   $usuario = limpiar( $_POST['usuario'] );
   $clave = $_POST['clave'];
   $nivel = (int)$_POST['nivel'];

   if ( $nombre == '' || $usuario == '' || $clave == '' )
   {
      $msg = '<p class="error">Todos los campos son obligatorios.</p>';
   }
   else
   {
      $claveHash = md5( $clave );
      $sql = "INSERT INTO usuarios (nombre, usuario, clave, nivel, activo) VALUES (" .
             "'" . mysql_real_escape_string($nombre) . "', " .
             "'" . mysql_real_escape_string($usuario) . "', " .
             "'" . $claveHash . "', " .
             $nivel . ", 1)";
      $ok = db_query( $sql );
      if ( $ok )
      {
         $msg = '<p class="ok">Usuario creado.</p>';
         logAccion( 'ALTA_USUARIO', $usuario );
      }
      else
      {
         $msg = '<p class="error">Error al crear el usuario (usuario duplicado?).</p>';
      }
   }
}

//
// deshabilitar / habilitar usuario
//
if ( isset($_GET['toggle']) && is_numeric($_GET['toggle']) )
{
   $idUsr = (int)$_GET['toggle'];
   $sqlGet = "SELECT activo FROM usuarios WHERE id_usuario = $idUsr";
   $rGet = db_query( $sqlGet );
   if ( $rGet && mysql_num_rows($rGet) == 1 )
   {
      $rowU = mysql_fetch_assoc( $rGet );
      $nuevoEstado = $rowU['activo'] == 1 ? 0 : 1;
      db_query( "UPDATE usuarios SET activo = $nuevoEstado WHERE id_usuario = $idUsr" );
      logAccion( 'TOGGLE_USUARIO', "id_usuario=$idUsr nuevo_estado=$nuevoEstado" );
   }
}

$resUsuarios = db_query( "SELECT id_usuario, nombre, usuario, nivel, activo FROM usuarios ORDER BY nombre" );

?>

<h2>Administracion de Usuarios</h2>

<?php echo $msg; ?>

<table class="datos" width="100%">
<tr><th>Nombre</th><th>Usuario</th><th>Nivel</th><th>Estado</th><th>&nbsp;</th></tr>
<?php
$i = 0;
if ( $resUsuarios && mysql_num_rows($resUsuarios) > 0 )
{
   while ( $u = mysql_fetch_assoc($resUsuarios) )
   {
      $clase = ( $i % 2 == 0 ) ? 'filaPar' : 'filaImpar';
      $estadoTxt = $u['activo'] == 1 ? '<span class="ok">activo</span>' : '<span class="error">inactivo</span>';
      echo "<tr class=\"$clase\">";
      echo "<td>" . $u['nombre'] . "</td>";
      echo "<td>" . $u['usuario'] . "</td>";
      echo "<td>" . $u['nivel'] . "</td>";
      echo "<td>" . $estadoTxt . "</td>";
      echo "<td><a href=\"usuarios.php?toggle=" . $u['id_usuario'] . "\">" . ($u['activo'] == 1 ? 'deshabilitar' : 'habilitar') . "</a></td>";
      echo "</tr>";
      $i++;
   }
}
?>
</table>

<br>

<table class="datos" width="50%">
<tr><th colspan="2">Nuevo Usuario</th></tr>
<tr><td colspan="2">
<form method="post" action="usuarios.php">
<table cellpadding="2" cellspacing="0" border="0" width="100%">
<tr><td>Nombre completo:</td><td><input type="text" name="nombre" size="25"></td></tr>
<tr><td>Usuario (login):</td><td><input type="text" name="usuario" size="15"></td></tr>
<tr><td>Clave:</td><td><input type="password" name="clave" size="15"></td></tr>
<tr><td>Nivel:</td><td>
<select name="nivel">
<option value="1">Consulta</option>
<option value="3">Deposito</option>
<option value="5">Vendedor</option>
<option value="9">Administrador</option>
</select>
</td></tr>
<tr><td colspan="2" align="center">
<input type="hidden" name="submit_usuario" value="1">
<input type="submit" value="Crear Usuario">
</td></tr>
</table>
</form>
</td></tr>
</table>

<?php
require_once( '../inc/footer.php' );
?>
