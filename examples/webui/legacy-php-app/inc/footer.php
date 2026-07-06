<?php
// footer.php
?>
</td></tr>
</table>
</td>
</tr>
<tr>
<td colspan="2" align="center" style="font-size:9px; color:#666666; padding:6px;">
GestPyme v<?php echo VERSION_SISTEMA; ?> &copy; 2003-2004 <?php echo NOMBRE_EMPRESA; ?> - Todos los derechos reservados
</td>
</tr>
</table>
</body>
</html>
<?php
// cerramos la conexion al final de la pagina (algunas paginas no llegan a llamar esto si hacen exit antes, no importa)
if ( isset($link) )
   mysql_close( $link );
?>
