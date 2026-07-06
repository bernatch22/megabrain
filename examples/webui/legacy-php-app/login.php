<?php
//-----------------------------------------------------------------------------
// login.php
// Pantalla de acceso al sistema
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );

$error = '';

if ( isset($_POST['submit_login']) && $_POST['submit_login'] == 'ingresar' )
{
   $usuario = limpiar( $_POST['usuario'] );
   $clave = $_POST['clave'];  // no limpiar antes de comparar hash

   //--------------------------------------------------------------------
   // CHUNK IMPORTANTE: validacion de login y permisos
   //
   // Busca el usuario, compara password (md5, como se hacia en 2003),
   // chequea que este activo, y si todo OK arma la sesion con su nivel
   // de acceso. El nivel se usa despues en toda la app para mostrar u
   // ocultar opciones (ver header.php e inc/funciones.php).
   //--------------------------------------------------------------------
   $usuarioEscaped = mysql_real_escape_string( $usuario );
   $sql = "SELECT id_usuario, nombre, clave, nivel, activo FROM usuarios WHERE usuario = '$usuarioEscaped'";
   $res = db_query( $sql );

   if ( $res && mysql_num_rows($res) == 1 )
   {
      $row = mysql_fetch_assoc( $res );

      if ( $row['activo'] != 1 )
      {
         $error = 'El usuario esta deshabilitado. Consulte con el administrador.';
      }
      else if ( md5($clave) == $row['clave'] )
      {
         // login OK
         $_SESSION['usuario'] = $row['nombre'];
         $_SESSION['id_usuario'] = $row['id_usuario'];
         $_SESSION['nivel'] = (int)$row['nivel'];
         $_SESSION['login_time'] = time();

         logAccion( 'LOGIN', 'Ingreso al sistema' );

         redireccionar( '/main.php' );
      }
      else
      {
         $error = 'Clave incorrecta.';
      }
   }
   else
   {
      $error = 'Usuario no encontrado.';
   }

   if ( $error != '' )
      logAccion( 'LOGIN_FALLIDO', "usuario=$usuario" );
}
?>
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<title>GestPyme - Ingreso</title>
<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
</head>
<body bgcolor="#ECE9D8">
<table width="100%" height="100%" cellpadding="0" cellspacing="0" border="0">
<tr><td align="center" valign="middle">

<table width="300" cellpadding="4" cellspacing="0" border="1" bordercolor="#003366" style="background-color:#FFFFFF;">
<tr><td bgcolor="#003366">
<font color="#FFFFFF" face="Verdana" size="2"><b>GestPyme - Ingreso al Sistema</b></font>
</td></tr>
<tr><td align="center">
<?php if ( $error != '' ) { ?>
<p class="error"><?php echo $error; ?></p>
<?php } ?>
<form method="post" action="login.php">
<table cellpadding="3" cellspacing="0" border="0">
<tr><td>Usuario:</td><td><input type="text" name="usuario" size="20"></td></tr>
<tr><td>Clave:</td><td><input type="password" name="clave" size="20"></td></tr>
<tr><td colspan="2" align="center">
<input type="hidden" name="submit_login" value="ingresar">
<input type="submit" value="Ingresar">
</td></tr>
</table>
</form>
</td></tr>
</table>

</td></tr>
</table>
</body>
</html>
