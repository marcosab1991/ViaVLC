
(function ($) {

    $(document).on("click", ".js-acciones-estacion", function (e) {

        $("#info-estacion").html("");
        $(".main-loader").fadeIn(300);

        let id = $(this).data("estacion");
        let data = "action=info-estacion&id=" + id;

        submitAjax(data, function (result) {

            if (result.error) {
                console.log(result);
            } else {
                $("#info-estacion").html(result["html"]);
                $(".main-loader").fadeOut(300);
                $("#link-info-estacion").click();
            }
        });

        e.preventDefault();
    });

}(jQuery));

