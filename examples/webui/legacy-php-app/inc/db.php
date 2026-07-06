<?php
//-----------------------------------------------------------------------------
// db.php
// Conexion a la base de datos - Sistema de Gestion Comercial "GestPyme"
// NO TOCAR SIN AVISAR A SOPORTE - ultima modificacion 14/03/2004
//-----------------------------------------------------------------------------

$GLOBALS['DB_HOST'] = 'localhost';
$GLOBALS['DB_USER'] = 'gestpyme_usr';
$GLOBALS['DB_PASS'] = 'gp2003clave';
$GLOBALS['DB_NAME'] = 'gestpyme_db';

$link = mysql_connect( $GLOBALS['DB_HOST'], $GLOBALS['DB_USER'], $GLOBALS['DB_PASS'] );
if ( !$link )
{
   die( 'No se pudo conectar: ' . mysql_error() );
}

$dbsel = mysql_select_db( $GLOBALS['DB_NAME'], $link );
if ( !$dbsel )
{
   die( 'No se pudo seleccionar la base: ' . mysql_error() );
}

// algunos paginas viejas usan $conexion en vez de $link, dejamos las dos por las dudas
$conexion = $link;

mysql_query( "SET NAMES 'latin1'" );

//-----------------------------------------------------------------------------
// helper corto para no repetir el die() en cada pagina (ver funciones.php para mas)
//-----------------------------------------------------------------------------
function db_query( $sql )
{
   global $link;
   $r = mysql_query( $sql, $link );
   if ( !$r )
   {
      echo "<!-- SQL ERROR: " . mysql_error( $link ) . " -->";
      echo "<!-- QUERY: " . $sql . " -->";
   }
   return $r;
}

?>
