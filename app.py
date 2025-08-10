    from flask import Flask, request
    import sqlite3
    from datetime import date
    import requests
    import os # <-- Tambahkan import ini

    # Inisialisasi aplikasi Flask
    app = Flask(__name__)

    # Path ke database akan berada di disk persisten Render
    # Pastikan path ini sesuai dengan yang Anda atur di Render
    DB_PATH = '/var/data/absensi_sekolah.db'
    
    # Dictionary untuk menyimpan status percakapan setiap pengguna (guru)
    user_states = {}

    # --- Fungsi Bantuan (Helpers) ---
    def get_db_connection():
        """Membuka koneksi baru ke database."""
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    def send_whatsapp_message(penerima, pesan):
        """
        Fungsi untuk mengirim pesan WhatsApp menggunakan API OneSender.
        """
        # Ambil URL dan API Key dari Environment Variables di Render
        ONESENDER_URL = os.environ.get("ONESENDER_URL")
        ONESENDER_API_KEY = os.environ.get("ONESENDER_API_KEY")

        if not ONESENDER_URL or not ONESENDER_API_KEY:
            print("!! ERROR: ONESENDER_URL atau ONESENDER_API_KEY tidak diatur di Environment Variables.")
            return

        headers = {
            "Authorization": f"Bearer {ONESENDER_API_KEY}",
            "Content-Type": "application/json"
        }
        
        nomor_tujuan = penerima.replace('+', '')

        payload = {
            "recipient_type": "individual",
            "to": nomor_tujuan,
            "type": "text",
            "text": {
                "body": pesan
            }
        }

        print(f">> MENGIRIM KE: {nomor_tujuan} via OneSender")
        try:
            response = requests.post(ONESENDER_URL, json=payload, headers=headers, timeout=10)
            response.raise_for_status()
            print(f">> RESPON ONESENDER: {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"!! GAGAL MENGIRIM PESAN: {e}")

    # --- Logika Inti Bot (Tidak ada perubahan di sini) ---
    # ... (Salin semua fungsi dari handle_start_command hingga handle_attendance_input dari kode sebelumnya) ...
    def handle_start_command(sender_id):
        conn = get_db_connection()
        daftar_kelas = conn.execute("SELECT id, nama_kelas FROM kelas ORDER BY nama_kelas").fetchall()
        conn.close()
        if not daftar_kelas:
            send_whatsapp_message(sender_id, "Maaf, belum ada data kelas di dalam sistem.")
            return
        pesan_balasan = "Selamat datang di Bot Absensi!  Attendance Bot\n\nSilakan pilih kelas yang akan diabsen dengan membalas *nomornya*:\n"
        for kelas in daftar_kelas:
            pesan_balasan += f"\n*{kelas['id']}*. {kelas['nama_kelas']}"
        user_states[sender_id] = {'state': 'menunggu_pilihan_kelas'}
        send_whatsapp_message(sender_id, pesan_balasan)

    def handle_class_selection(sender_id, message_body):
        try:
            kelas_id = int(message_body)
        except ValueError:
            send_whatsapp_message(sender_id, "Input tidak valid. Mohon balas dengan *angka* (nomor kelas).")
            return
        conn = get_db_connection()
        daftar_siswa = conn.execute("SELECT id, nama_lengkap FROM siswa WHERE kelas_id = ?", (kelas_id,)).fetchall()
        nama_kelas_terpilih = conn.execute("SELECT nama_kelas FROM kelas WHERE id = ?", (kelas_id,)).fetchone()
        conn.close()
        if not daftar_siswa:
            send_whatsapp_message(sender_id, f"Tidak ditemukan siswa di kelas tersebut. Sesi dibatalkan.")
            user_states.pop(sender_id, None)
            return
        user_states[sender_id] = {
            'state': 'proses_absen',
            'kelas_id': kelas_id,
            'nama_kelas': nama_kelas_terpilih['nama_kelas'],
            'siswa_list': [dict(siswa) for siswa in daftar_siswa],
            'current_student_index': 0
        }
        send_whatsapp_message(sender_id, f"Baik, memulai absensi untuk kelas *{nama_kelas_terpilih['nama_kelas']}*.")
        ask_next_student_status(sender_id)

    def ask_next_student_status(sender_id):
        state = user_states[sender_id]
        index = state['current_student_index']
        siswa = state['siswa_list'][index]
        pesan = (
            f"({index + 1}/{len(state['siswa_list'])}) Absensi untuk: *{siswa['nama_lengkap']}*\n\n"
            "Balas dengan nomor:\n"
            "*1*. Hadir ✅\n"
            "*2*. Sakit 🤒\n"
            "*3*. Izin ✉️\n"
            "*4*. Alpa ❌\n\n"
            "Ketik `!batal` untuk menghentikan sesi ini."
        )
        send_whatsapp_message(sender_id, pesan)

    def handle_attendance_input(sender_id, message_body):
        status_map = {'1': 'Hadir', '2': 'Sakit', '3': 'Izin', '4': 'Alpa'}
        status = status_map.get(message_body)
        if not status:
            send_whatsapp_message(sender_id, "Pilihan tidak valid. Mohon balas dengan angka 1, 2, 3, atau 4.")
            ask_next_student_status(sender_id)
            return
        state = user_states[sender_id]
        index = state['current_student_index']
        siswa_id = state['siswa_list'][index]['id']
        conn = get_db_connection()
        conn.execute(
            "INSERT INTO absensi (siswa_id, tanggal, status, dicatat_oleh) VALUES (?, ?, ?, ?)",
            (siswa_id, date.today(), status, sender_id)
        )
        conn.commit()
        conn.close()
        state['current_student_index'] += 1
        if state['current_student_index'] < len(state['siswa_list']):
            ask_next_student_status(sender_id)
        else:
            nama_kelas = state['nama_kelas']
            send_whatsapp_message(sender_id, f"🎉 Absensi untuk kelas *{nama_kelas}* telah selesai. Terima kasih!")
            user_states.pop(sender_id, None)


    # --- Webhook Endpoint ---
    @app.route('/webhook', methods=['POST'])
    def webhook():
        try:
            incoming_data = request.get_json()
            sender_id = incoming_data.get('from') 
            message_body = incoming_data.get('message', {}).get('text', {}).get('body', '').strip()
        except Exception:
            sender_id = None
            message_body = None

        if not sender_id or not message_body:
            return 'Invalid payload', 400

        print(f"Pesan masuk dari [{sender_id}]: '{message_body}'")
        if message_body.lower() == '!batal':
            user_states.pop(sender_id, None)
            send_whatsapp_message(sender_id, "Sesi absensi telah dibatalkan.")
            return 'OK', 200
        current_state_info = user_states.get(sender_id)
        state = current_state_info['state'] if current_state_info else None
        if state == 'menunggu_pilihan_kelas':
            handle_class_selection(sender_id, message_body)
        elif state == 'proses_absen':
            handle_attendance_input(sender_id, message_body)
        else:
            if message_body.lower() == '!absen':
                handle_start_command(sender_id)
            else:
                pesan_bantuan = "Perintah tidak dikenali. Ketik `!absen` untuk memulai."
                send_whatsapp_message(sender_id, pesan_bantuan)
        return 'OK', 200

    # Jalankan setup database jika file belum ada
    @app.before_first_request
    def setup_initial_database():
        if not os.path.exists(DB_PATH):
            print("File database tidak ditemukan. Membuat database baru...")
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("CREATE TABLE IF NOT EXISTS kelas (id INTEGER PRIMARY KEY, nama_kelas TEXT NOT NULL UNIQUE);")
            cursor.execute("CREATE TABLE IF NOT EXISTS siswa (id INTEGER PRIMARY KEY, nama_lengkap TEXT NOT NULL, kelas_id INTEGER NOT NULL);")
            cursor.execute("CREATE TABLE IF NOT EXISTS absensi (id INTEGER PRIMARY KEY, siswa_id INTEGER NOT NULL, tanggal DATE NOT NULL, status TEXT NOT NULL);")
            
            # Isi data contoh
            cursor.executemany("INSERT INTO kelas (nama_kelas) VALUES (?)", [('10-A MIPA',), ('11-B IPS',)])
            cursor.executemany("INSERT INTO siswa (nama_lengkap, kelas_id) VALUES (?, ?)", [('Budi Darmawan', 1), ('Citra Lestari', 1), ('Dewi Sartika', 2)])
            conn.commit()
            conn.close()
            print("Database dan data contoh berhasil dibuat.")

    if __name__ == '__main__':
        # Port akan diatur oleh Render secara otomatis
        port = int(os.environ.get('PORT', 5000))
        app.run(host='0.0.0.0', port=port)
    
