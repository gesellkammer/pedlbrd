# -*- mode: python -*-
def Datafiles(*filenames, **kw):
    import os
    
    def datafile(path, strip_path=True):
        parts = path.split('/')
        path = name = os.path.join(*parts)
        if strip_path:
            name = os.path.basename(path)
        return name, path, 'DATA'

    strip_path = kw.get('strip_path', True)
    return TOC(
        datafile(filename, strip_path=strip_path)
        for filename in filenames
        if os.path.isfile(filename))

a = Analysis(['pedlbrd.py'],
             pathex=['/home/em/dev/pedlbrd'],
             hiddenimports=[],
             hookspath=None,
             runtime_hooks=None)

pyz = PYZ(a.pure)
exe = EXE(pyz,
          a.scripts,
          exclude_binaries=True,
          name='pedlbrd',
          debug=False,
          strip=None,
          upx=True,
          console=True )

datafiles = Datafiles(
  'assets/pedlbrd-icon.png', 
  'extra/pedlbrd.desktop',
  'pedltalk.py'
)

coll = COLLECT(exe,
               a.binaries,
               a.zipfiles,
               a.datas,
               datafiles,

               strip=None,
               upx=True,

               name='pedlbrd')
