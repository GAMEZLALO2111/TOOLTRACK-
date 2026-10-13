import barcode
from barcode.writer import ImageWriter

# Configuración de campos para punzón y matriz
PUNCH_FIELDS = ["ID", "TN", "SIZE", "TYPE", "LIFE", "SO"]
DIE_FIELDS = ["ID", "TN", "SIZE", "TYPE", "LIFE", "CLEARANCE", "SO"]

def generate_barcode(data_list, filename):
    data_str = "|".join(data_list)
    code128 = barcode.get('code128', data_str, writer=ImageWriter())
    code128.save(filename)
    print(f"Barcode saved as {filename}.png with data: {data_str}")

def main():
    print("Barcode Generator for Punches and Dies")
    tipo = input("Type (punch/die): ").strip().lower()
    if tipo == "punch":
        fields = PUNCH_FIELDS
    elif tipo == "die":
        fields = DIE_FIELDS
    else:
        print("Invalid type. Use 'punch' or 'die'.")
        return
    data = []
    for field in fields:
        val = input(f"{field}: ").strip()
        data.append(val)
    filename = input("Output filename (without extension): ").strip()
    generate_barcode(data, filename)

if __name__ == "__main__":
    main()
