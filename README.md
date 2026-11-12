# Discord + BetterDiscord cho Arch Linux

Bộ cài cho Discord Stable từ Arch Extra trên **Arch Linux x86_64**. Bản này tái tạo BetterDiscord 1.14.5 và launcher ưu tiên Intel Iris Xe trên máy có cả Iris Xe và NVIDIA. Máy có GPU khác dùng cấu hình encoder mặc định của Discord.

**Bản preview.** Đã kiểm tra cài và gỡ với Discord 1.0.169 trên profile Arch sạch. Screenshare Discord thật có người xem và thử trên một máy Arch có GPU khác vẫn đang chờ nghiệm thu.

## Cài đặt

Trong thư mục vừa clone, thoát Discord hoàn toàn nếu ứng dụng đang chạy, rồi chạy:

```bash
./setup.sh
```

Bộ cài gọi pacman nếu thiếu package, tải runtime qua bootstrap Discord chính thức và build BetterDiscord từ mã nguồn đã pin. Sau khi cài, mở **Discord (BetterDiscord)** trong menu ứng dụng và đăng nhập như bình thường. Cần Internet và quyền sudo để cài package còn thiếu. Pacman dùng database package hiện có của Arch.

Nếu muốn cài và bật **YABDP4Nitro**, dùng lệnh sau ở lần cài đầu hoặc khi đã cài BetterDiscord:

```bash
./setup.sh --with-yabd4
```

Preset mới dùng H.264, 1080p60 và bitrate mặc định. BetterDiscord giữ plugin, thiết lập và trạng thái bật/tắt hiện có của bạn. Bản plugin mới được pin ở 7.0.10 và tắt tự cập nhật trong preset do bộ cài tạo. Chất lượng stream thực tế phụ thuộc vào Discord, phần cứng và kết nối.

Bộ cài nhận runtime Stable **1.0.168** và **1.0.169** theo checksum của archive gốc. Phiên bản pacman có thể khác phiên bản runtime. Nếu self-updater chuyển sang build chưa hỗ trợ, launcher mở Discord theo đường mặc định và báo trạng thái. Chạy lại bộ cài sau khi repo thêm hỗ trợ cho build mới.

## Gỡ cài đặt

Thoát Discord rồi chạy:

```bash
./setup.sh --uninstall
```

Bộ cài khôi phục các file còn khớp với trạng thái đã ghi lúc cài. File đã sửa được giữ lại. Nếu loader bị chỉnh sau khi cài, các file nó còn cần cũng được giữ để Discord có thể khởi động. Package Discord, đăng nhập và profile vẫn nằm trên máy. Bản ghi dùng để khôi phục nằm trong `${XDG_STATE_HOME:-~/.local/state}/discord-arch-patched`.

## Nguồn và kết quả đối chiếu

| Thành phần | Nguồn và xác minh |
| --- | --- |
| Discord Stable | [Arch Extra](https://archlinux.org/packages/extra/x86_64/discord/files/) cung cấp bootstrap. [Công thức Flathub cho 1.0.168](https://github.com/flathub/com.discordapp.Discord/blob/78203adc691757e2cd2daa15c02ebce81e96c562/com.discordapp.Discord.yaml) ghi checksum runtime và module voice từ CDN Discord. |
| BetterDiscord | [Commit bc75c6f6](https://github.com/BetterDiscord/BetterDiscord/commit/bc75c6f612355b7d63b19c08e9211a6ffc654171), build production bằng Bun với lockfile upstream. SHA-256 của source là `5d05928a4edcfb6c3024da100480f8862e0f6eacb35a67e5effda9f84284525e`. ASAR build lại khớp từng byte với bản local, SHA-256 `3f9c9a1588813dd3b4ee1f573157da9b93db474286fd0a28603b0e3fafe71655`. Asset GitHub gắn tag 1.14.5 được kiểm tra chứa build 1.14.6. |
| YABDP4Nitro 7.0.10 | [Commit 3934168f](https://github.com/riolubruh/YABDP4Nitro/blob/3934168fd50ec95c76c5c9d73af0158290048870/YABDP4Nitro.plugin.js), SHA-256 `e9a1ad76a47c418116dc3f37129989b675476d7ec6f6d2645478087c8756b26d`, [OSL-3.0](https://github.com/riolubruh/YABDP4Nitro/blob/main/LICENSE.md). |

Cả 561 file trong `discord_voice/1` khớp bản gốc, gồm `discord_voice.node`, `gpu_encoder_helper` và `index.js`. Launcher thực hiện cấu hình encoder theo tiến trình bằng Bubblewrap, nhận diện PCI vendor qua sysfs và kiểm tra H.264 encode bằng VA-API iHD. Khi chuyển từ setup có hai injection đã nhận diện, bộ cài khôi phục desktop core về file gốc, dùng loader ở `resources/app` và chuyển các mục menu cũ trỏ đến `discord-irisxe` sang launcher mới.

Discord và các dependency được lấy từ nguồn upstream khi cài. Client mod và các plugin như YABDP4Nitro chịu các giới hạn sử dụng nêu trong [điều khoản Discord](https://discord.com/terms#software-in-discords-services).

## Kiểm thử

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
shellcheck setup.sh
ruff check --no-cache discord_patch.py tests
```

Để xác nhận encoder của Discord trên Iris Xe + NVIDIA, mở launcher đã cài, screenshare H.264 1080p60 với một người xem và kiểm tra tải ở engine **Video** của GPU Intel trong lúc stream, chẳng hạn bằng `intel_gpu_top`. Trên máy Arch có GPU khác, kiểm tra launcher mở Discord và dùng encoder mặc định.
