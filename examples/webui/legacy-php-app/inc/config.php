<?php
//-----------------------------------------------------------------------------
// config.php
// Configuracion general del sistema GestPyme
//-----------------------------------------------------------------------------

error_reporting( E_ALL & ~E_NOTICE & ~E_DEPRECATED );
ini_set( 'display_errors', '1' );  // TODO: sacar esto en produccion final!!

session_start();

define( 'RUTA_BASE', dirname(__FILE__) . '/..' );
define( 'NOMBRE_EMPRESA', 'GestPyme S.R.L.' );
define( 'VERSION_SISTEMA', '1.4.2' );

// tasa de IVA vigente (Ley 23349 y modificatorias) - cambiar aca si cambia la alicuota general
$GLOBALS['IVA_PORCENTAJE'] = 21;  // %

// stock minimo por defecto si el producto no tiene uno propio cargado
$GLOBALS['STOCK_MINIMO_DEFAULT'] = 5;

// niveles de usuario
define( 'NIVEL_ADMIN', 9 );
define( 'NIVEL_VENDEDOR', 5 );
define( 'NIVEL_DEPOSITO', 3 );
define( 'NIVEL_CONSULTA', 1 );

$rutaImagenes = '/img/';
$rutaCss = '/css/estilos.css';

require_once( dirname(__FILE__) . '/db.php' );
require_once( dirname(__FILE__) . '/funciones.php' );

?>
