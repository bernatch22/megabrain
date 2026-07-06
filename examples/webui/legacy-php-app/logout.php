<?php
require_once( 'inc/config.php' );
if ( isset($_SESSION['usuario']) )
{
   logAccion( 'LOGOUT', '' );
}
$_SESSION = array();
session_destroy();
header( "Location: /login.php" );
exit;
?>
