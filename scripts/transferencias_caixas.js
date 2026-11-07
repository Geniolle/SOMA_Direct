$(document).ready(function () {
    $('.date').datepicker();
    $('#pesquisar').click();
});

$('#exampleStandardForm').submit(function (event) {
    buscarTransferencias();
});


$(document).on('click', '.bnt_excluir', function (event) {
    var id = $(this).attr('id');
    var linha = $(this);
    swal({
        type: "info",
        title: 'Deseja realmente excluir? ',
        showCancelButton: true,
        confirmButtonText: 'Sim',
        animation: "slide-from-top",
    }).then(function (result) {
        $.post('sys/app/transferencias_caixas.php', {id: id, excluir: 1}, function (data) {
            console.log(data);
            switch (parseInt(data.status)) {
                case 1:
                    linha.closest('tr').remove();
                    swal({type: 'success', title: 'Sucesso', text: 'Item foi excluido com sucesso!', showLoaderOnConfirm: true, timer: 3000});
                    break;
                case 3:
                    swal({type: 'error', title: 'Ops!', text: 'Não é possível excluir contas registradas via arquivo OFX.', timer: 5000});
                    break;
                case 7:
                    swal("Erro", "A sessão expirou, a pagina será atualizada !", "error");
                    setTimeout(function () {
                        location.reload();
                    }, 3000);
                    break;
                default :
                    swal({type: 'error', title: 'Erro', text: 'Item não foi excluido,tente novamente!', timer: 3000});
                    break;
            }
        }, 'json');
    }, function (dismiss) {
        if (dismiss === 'cancel') {
            swal('Cancelado', 'Exclusão do registro foi cancelada :)', 'error')
        }
    });
});

$(document).on('click', '.excluir', function (event) {
    var first = false;
    var id = '';
    $('input[name="check[]"]:checked').each(function () {
        if ($(this).is(':checked')) {
            if (first) {
                id += ",";
            }
        }
        id += $(this).val();
        first = true;
    });

    //  var id = $(this).attr('id');
//    var linha = $(this);
    swal({
        type: "info",
        title: 'Deseja realmente excluir? ',
        showCancelButton: true,
        confirmButtonText: 'Sim',
        animation: "slide-from-top",
    }).then(function (result) {
        $.post('sys/app/transferencias_caixas.php', {id: id, excluir: 1}, function (data) {
            //console.log(data);
            switch (parseInt(data.status)) {
                case 1:
                    $.each(id.split(","), function (i, l) {
                        $('#check' + l).closest('tr').remove();
                    });
                    swal({type: 'success', title: 'Sucesso', text: 'Item foi excluido com sucesso!', showLoaderOnConfirm: true, timer: 3000});
                    break;
                case 3:
                    swal({type: 'error', title: 'Ops!', text: 'Não é possível excluir contas registradas via arquivo OFX.', timer: 5000});
                    break;
                case 7:
                    swal("Erro", "A sessão expirou, a pagina será atualizada !", "error");
                    setTimeout(function () {
                        location.reload();
                    }, 3000);
                    break;
                default :
                    swal({type: 'error', title: 'Erro', text: 'Item não foi excluido,tente novamente!', timer: 3000});
                    break;
            }
        }, 'json');
    }, function (dismiss) {
        if (dismiss === 'cancel') {
            swal('Cancelado', 'Exclusão do registro foi cancelada :)', 'error')
        }
    });



});



$('.btn_editar').on('click', function () {
    location.href = "?mod=ivv&exec=transferencias_caixas_dados&ID=" + $('input[name="check[]"]:checked').val();
});

$(document).on('click', '#select_all', function () {
    if ($(this).is(':checked')) {
        $('.selectable-item').prop("checked", true);
        $('.site-action').addClass('active');
        $('.btn_editar').addClass('hide');
    } else {
        $('.selectable-item').prop("checked", false);
        $('.site-action').removeClass('active');
    }
});

$(document).on('click', '.selectable-item', function () {
    var tam = $('.selectable-item:checked').length;
    if (tam > 0) {
        if (tam > 1) {
            $('.btn_editar').addClass('hide');
        } else {
            $('.btn_editar').removeClass('hide');
        }
        $('.site-action').addClass('active');
    } else {
        $('.site-action').removeClass('active');
    }
})

$(document).on('change', 'select[name=id_inst]', function () {
    buscarTransferencias();
});

function buscarTransferencias() {
    event.preventDefault();
    $('#table').html('<div class="col-md-12 msg"><div class="alert dark alert-primary alert-dismissible" role="alert"> Carregando ... </div></div>');
    $.post("sys/post/buscarTransferenciasCaixas.php", {id_inst: $('select[name="id_inst"]').val(), i: $('input[name="vencimento_inicio"]').val(), f: $('input[name="vencimento_fim"]').val()}, function (data) {
        $("#table").html(data);
//        $('.LABEL').each(function (index, item) {
//            var t = $(this).attr("class");
//            $(this).attr("class", "");
//            $(this).attr("class", t.toLowerCase());
//        });
    });
}
$(document).on('click', '.btn_obs', function () {
    console.log('AQUI');
    var t = $(this).attr('data-dados');
   t =( t !=''?t:'Nenhuma observação inserida');
    $('.obs-texto').html(t);
    var m = $('#obs');
    m.modal('show');
});
