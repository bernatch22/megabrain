<?php
//-----------------------------------------------------------------------------
// instalar.php
// Script de instalacion inicial - crea tablas si no existen y carga un
// usuario admin por defecto. Correr UNA sola vez y despues borrar o
// renombrar este archivo (¡¡no lo dejes en el server de produccion!!)
//-----------------------------------------------------------------------------
require_once( 'inc/db.php' );

echo "<pre>";
echo "Instalando GestPyme...\n\n";

$tablas = array();

$tablas['usuarios'] = "CREATE TABLE IF NOT EXISTS usuarios (
   id_usuario INT AUTO_INCREMENT PRIMARY KEY,
   nombre VARCHAR(100) NOT NULL,
   usuario VARCHAR(50) NOT NULL UNIQUE,
   clave VARCHAR(32) NOT NULL,
   nivel INT NOT NULL DEFAULT 1,
   activo TINYINT NOT NULL DEFAULT 1
) ENGINE=MyISAM";

$tablas['clientes'] = "CREATE TABLE IF NOT EXISTS clientes (
   id_cliente INT AUTO_INCREMENT PRIMARY KEY,
   nombre VARCHAR(150) NOT NULL,
   cuit VARCHAR(20),
   direccion VARCHAR(200),
   telefono VARCHAR(30),
   categoria VARCHAR(20) NOT NULL DEFAULT 'MINORISTA',
   fecha_alta DATETIME
) ENGINE=MyISAM";

$tablas['productos'] = "CREATE TABLE IF NOT EXISTS productos (
   id_producto INT AUTO_INCREMENT PRIMARY KEY,
   codigo VARCHAR(30) NOT NULL UNIQUE,
   descripcion VARCHAR(150) NOT NULL,
   precio_costo DECIMAL(10,2) NOT NULL DEFAULT 0,
   precio_venta DECIMAL(10,2) NOT NULL DEFAULT 0,
   stock_actual INT NOT NULL DEFAULT 0,
   stock_minimo INT NOT NULL DEFAULT 5
) ENGINE=MyISAM";

$tablas['pedidos'] = "CREATE TABLE IF NOT EXISTS pedidos (
   id_pedido INT AUTO_INCREMENT PRIMARY KEY,
   id_cliente INT NOT NULL,
   fecha DATE NOT NULL,
   estado VARCHAR(20) NOT NULL DEFAULT 'PENDIENTE',
   fecha_confirmacion DATETIME
) ENGINE=MyISAM";

$tablas['pedido_detalle'] = "CREATE TABLE IF NOT EXISTS pedido_detalle (
   id_detalle INT AUTO_INCREMENT PRIMARY KEY,
   id_pedido INT NOT NULL,
   id_producto INT NOT NULL,
   cantidad INT NOT NULL
) ENGINE=MyISAM";

$tablas['facturas'] = "CREATE TABLE IF NOT EXISTS facturas (
   id_factura INT AUTO_INCREMENT PRIMARY KEY,
   id_pedido INT NOT NULL,
   id_cliente INT NOT NULL,
   fecha DATETIME NOT NULL,
   subtotal DECIMAL(10,2) NOT NULL,
   descuento_porc DECIMAL(5,2) NOT NULL DEFAULT 0,
   descuento_monto DECIMAL(10,2) NOT NULL DEFAULT 0,
   iva DECIMAL(10,2) NOT NULL,
   total DECIMAL(10,2) NOT NULL,
   anulada TINYINT NOT NULL DEFAULT 0
) ENGINE=MyISAM";

$tablas['log_acciones'] = "CREATE TABLE IF NOT EXISTS log_acciones (
   id_log INT AUTO_INCREMENT PRIMARY KEY,
   usuario VARCHAR(50),
   accion VARCHAR(50),
   detalle VARCHAR(255),
   fecha DATETIME
) ENGINE=MyISAM";

foreach ( $tablas as $nombre => $sql )
{
   $r = mysql_query( $sql, $link );
   if ( $r )
      echo "OK  - tabla '$nombre' creada/verificada\n";
   else
      echo "ERROR - tabla '$nombre': " . mysql_error($link) . "\n";
}

// usuario admin por defecto (usuario: admin / clave: admin123)
$chk = mysql_query( "SELECT id_usuario FROM usuarios WHERE usuario = 'admin'", $link );
if ( $chk && mysql_num_rows($chk) == 0 )
{
   $claveHash = md5( 'admin123' );
   mysql_query( "INSERT INTO usuarios (nombre, usuario, clave, nivel, activo) VALUES ('Administrador', 'admin', '$claveHash', 9, 1)", $link );
   echo "\nUsuario admin creado (usuario: admin / clave: admin123) - CAMBIAR LA CLAVE!\n";
}

echo "\nListo.\n";
echo "</pre>";
?>
