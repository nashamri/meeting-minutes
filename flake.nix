{
  description = "Capture and summarize meeting minutes";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-parts.url = "github:hercules-ci/flake-parts";
  };

  outputs =
    inputs@{ flake-parts, ... }:
    flake-parts.lib.mkFlake { inherit inputs; } {
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];

      perSystem =
        { pkgs, lib, ... }:
        let
          inherit (pkgs) stdenv;

          # Single source of truth for the version: pyproject.toml.
          version = (builtins.fromTOML (builtins.readFile ./pyproject.toml)).project.version;

          # Linux-only: runtime libs that PyPI-installed PySide6 / QtWebEngine
          # need but can't find on NixOS (they're linked against system .so
          # names not at FHS paths). Exported as NIX_LD_LIBRARY_PATH in the
          # devshell. On Darwin pywebview uses WKWebView, so the list collapses.
          guiLibs = lib.optionals stdenv.isLinux (
            with pkgs;
            [
              stdenv.cc.cc.lib
              glib
              libGL
              libxkbcommon
              fontconfig
              freetype
              dbus
              zlib
              krb5
              libx11
              libxcb
              libxext
              libxrender
              libxrandr
              libxi
              libxcursor
              libxfixes
              libxtst
              libxcomposite
              libxdamage
              libxcb-util
              libxcb-image
              libxcb-keysyms
              libxcb-render-util
              libxcb-wm
              libxcb-cursor
              libxkbfile
              nss
              nspr
              alsa-lib
              expat
              cups
              pango
              cairo
              gtk3
              gdk-pixbuf
              atk
              libdrm
              mesa
              libgbm
              brotli
              wayland
              vulkan-loader
            ]
          );

          python = pkgs.python3;

          pythonEnv = python.withPackages (
            ps:
            with ps;
            [
              platformdirs
              pywebview
              nicegui
            ]
            ++ lib.optionals stdenv.isLinux [
              pyside6
              qtpy
            ]
          );

          app = stdenv.mkDerivation {
            pname = "meeting-minutes";
            inherit version;
            src = ./.;

            nativeBuildInputs = [ pkgs.makeWrapper ];
            dontConfigure = true;
            dontBuild = true;

            installPhase = ''
              runHook preInstall

              mkdir -p $out/share/meeting-minutes $out/bin
              cp main.py webapp.py gui_main.py models.py typst_io.py pyproject.toml \
                $out/share/meeting-minutes/
              if [ -d assets ]; then
                cp -r assets $out/share/meeting-minutes/
              fi

              makeWrapper ${pythonEnv}/bin/python $out/bin/meeting-minutes \
                --add-flags "$out/share/meeting-minutes/main.py" \
                --prefix PATH : ${lib.makeBinPath [ pkgs.typst ]}

              runHook postInstall
            '';

            meta = {
              description = "Capture and summarize meeting minutes";
              homepage = "https://github.com/nashamri/meeting-minutes";
              mainProgram = "meeting-minutes";
              platforms = lib.platforms.unix;
            };
          };
        in
        {
          packages.default = app;
          packages.meeting-minutes = app;

          devShells.default = pkgs.mkShell {
            packages = [ pkgs.uv pkgs.typst ];

            shellHook =
              (lib.optionalString stdenv.isLinux ''
                export NIX_LD_LIBRARY_PATH=${lib.makeLibraryPath guiLibs}''${NIX_LD_LIBRARY_PATH:+:$NIX_LD_LIBRARY_PATH}
              '')
              + ''
                # The `linux` extra carries a sys_platform == 'linux' marker, so
                # passing it on macOS is a no-op — keeps the hook simple.
                uv sync --extra linux
                source .venv/bin/activate
              '';
          };
        };
    };
}
