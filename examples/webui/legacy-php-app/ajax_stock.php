<?php
//-----------------------------------------------------------------------------
// ajax_stock.php
// Devuelve el stock actual de un producto en texto plano, para el
// JS de pedido_nuevo.php (ver funcion consultarStock() en el head... en
// realidad quedo sin usar despues del rediseño de 2004, dejar por las dudas)
//-----------------------------------------------------------------------------
require_once( 'inc/config.php' );

header( 'Content-Type: text/plain' );

if ( !isset($_SESSION['usuario']) )
{
   echo '0';
   exit;
}

if ( !isset($_GET['id']) || !is_numeric($_GET['id']) )
{
   echo '0';
   exit;
}

$id = (int)$_GET['id'];
$sql = "SELECT stock_actual FROM productos WHERE id_producto = $id";
$res = db_query( $sql );

if ( $res && mysql_num_rows($res) == 1 )
{
   $row = mysql_fetch_assoc( $res );
   echo $row['stock_actual'];
}
else
{
   echo '0';
}
?>
