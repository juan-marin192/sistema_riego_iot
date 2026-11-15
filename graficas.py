import sqlite3
import os
import matplotlib.pyplot as plt

def generar_grafica_telemetria():
    # 1. Definición y creación de la carpeta de destino si no existe
    directorio_destino = "registros"
    os.makedirs(directorio_destino, exist_ok=True)
    
    # 2. Conexión a la base de datos relacional SQLite
    conn = sqlite3.connect("riego_iot.db")
    cursor = conn.cursor()
    
    cursor.execute("""
        SELECT fecha_hora, humedad_vwc, umbral_trigger, bomba_estado 
        FROM telemetria 
        ORDER BY id ASC
    """)
    datos = cursor.fetchall()
    conn.close()
    
    if not datos:
        print("No se encontraron registros en la base de datos 'riego_iot.db'.")
        return
        
    # 3. Procesamiento y estructuración de vectores de telemetría
    fechas = [d[0].split(" ")[1] if " " in d[0] else d[0] for d in datos]
    vwc = [d[1] for d in datos]
    bomba = [100 if d[3] == "ON" else 0 for d in datos]
    
    # 4. Generación y renderizado del gráfico estadístico
    plt.figure(figsize=(10, 5))
    plt.plot(fechas, vwc, label="Humedad Volumétrica (VWC %)", color="blue", linewidth=2)
    plt.axhline(y=27, color="red", linestyle="--", label="Umbral MAD (27%)")
    plt.fill_between(fechas, 0, bomba, color="cyan", alpha=0.3, label="Estado Actuador (BOMBA ON)")
    
    plt.title("Evolución Edafoclimática y Ciclos de Riego IoT", fontsize=12, fontweight="bold")
    plt.xlabel("Estampa de Tiempo (Hora)", fontsize=10)
    plt.ylabel("Humedad Volumétrica VWC (%)", fontsize=10)
    plt.xticks(rotation=45, fontsize=8)
    plt.grid(True, linestyle=":", alpha=0.6)
    plt.legend(loc="upper right")
    
    # 5. Exportación de la imagen a la carpeta 'registros/'
    ruta_salida = os.path.join(directorio_destino, "reporte_telemetria.png")
    plt.tight_layout()
    plt.savefig(ruta_salida, dpi=300)
    print(f"Gráfica generada exitosamente en la ruta: '{ruta_salida}'.")

if __name__ == "__main__":
    generar_grafica_telemetria()