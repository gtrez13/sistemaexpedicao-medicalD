from services.emitir_via_rpa import emitir_nfe_por_ml_id

ML_ID_TESTE = "2000015276107080"  # coloque um pedido de teste aqui

print("\n==============================")
print("TESTE EMISSÃO VIA RPA")
print("==============================\n")

try:

    resultado = emitir_nfe_por_ml_id(
        ml_any_id=ML_ID_TESTE,
        headless=False  # False = abre navegador (bom para debug)
    )

    print("\n==============================")
    print("RESULTADO")
    print("==============================")

    print(resultado)

    if resultado.get("pdf"):
        print("\nPDF GERADO:", resultado["pdf"])

    print("\n✅ TESTE FINALIZADO")

except Exception as e:

    print("\n❌ ERRO NO TESTE")
    print(e)