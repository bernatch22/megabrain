<?php
// header.php - se incluye en (casi) todas las paginas despues de config.php
if ( !isset($_SESSION['usuario']) && basename($_SERVER['PHP_SELF']) != 'login.php' )
{
   header( "Location: /login.php" );
   exit;
}
?>
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.01 Transitional//EN">
<html>
<head>
<title>GestPyme - Sistema de Gestion</title>
<meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
<link rel="stylesheet" type="text/css" href="<?php echo $rutaCss; ?>">
<style>
body { font-family: Verdana, Arial, sans-serif; font-size: 11px; background-color: #ECE9D8; margin: 0; }
.titulo { background-color: #003366; color: #FFFFFF; font-weight: bold; padding: 4px; }
.menu { background-color: #D4D0C8; }
.menu a { color: #000080; text-decoration: none; font-weight: bold; margin-right: 10px; }
.menu a:hover { text-decoration: underline; }
table.datos { border-collapse: collapse; width: 100%; }
table.datos td, table.datos th { border: 1px solid #999999; padding: 3px; font-size: 11px; }
table.datos th { background-color: #C0C0C0; }
.filaPar { background-color: #FFFFFF; }
.filaImpar { background-color: #F0F0F0; }
.error { color: #CC0000; font-weight: bold; }
.ok { color: #006600; font-weight: bold; }
</style>
</head>
<body>
<table width="100%" cellpadding="0" cellspacing="0" border="0">
<tr>
<td class="titulo" colspan="2">
GestPyme - Sistema de Gestion Comercial &nbsp;|&nbsp;
<?php
if ( isset($_SESSION['usuario']) )
{
   echo "Usuario: " . $_SESSION['usuario'] . " (nivel " . $_SESSION['nivel'] . ")";
   echo " &nbsp;|&nbsp; <a href=\"/logout.php\" style=\"color:#FFFF99;\">Salir</a>";
}
?>
</td>
</tr>
<tr>
<td class="menu" colspan="2">
<a href="/main.php">Inicio</a>
<a href="/clientes.php">Clientes</a>
<a href="/pedidos.php">Pedidos</a>
<a href="/facturacion.php">Facturacion</a>
<a href="/inventario.php">Inventario</a>
<a href="/reportes.php">Reportes</a>
<?php if ( isset($_SESSION['nivel']) && $_SESSION['nivel'] >= NIVEL_ADMIN ) { ?>
<a href="/admin/usuarios.php">Admin</a>
<?php } ?>
</td>
</tr>
<tr>
<td colspan="2">
<table width="100%" cellpadding="10" cellspacing="0" border="0">
<tr><td valign="top">
